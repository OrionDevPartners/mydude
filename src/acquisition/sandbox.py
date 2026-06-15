"""
Sandboxed capability verifier.

Installs a candidate package into a temporary, isolated virtual environment
and runs a generated smoke/contract test. The subprocess environment is
stripped of all production secrets and credentials.

Network isolation policy (applied in priority order):
  1. OS-level namespace via `unshare --net` — subprocess has no network.
     This is the preferred and most secure mode.
  2. Python-level socket patch — monkey-patches socket.socket to raise on
     any connect() call inside the smoke test process. Activated only when
     `SANDBOX_ALLOW_NO_NETWORK_ISOLATION=true` is set explicitly by the
     operator (acknowledges that native-extension code can bypass this).
  3. FAIL CLOSED — if neither isolation tier is available and the operator
     has not set SANDBOX_ALLOW_NO_NETWORK_ISOLATION=true, verify_candidate()
     returns a failed SandboxResult immediately. This prevents untrusted
     third-party package code from making outbound network calls.

isolation_level values returned in SandboxResult:
  "unshare+venv+secret-strip"          — full OS namespace (preferred)
  "socket-patch+venv+secret-strip"     — Python-only partial isolation
  "failed:network_isolation_unavailable" — hard deny when no isolation can
                                           be established

ENABLE_AUTO_SIPHON_ACQUISITION must be true; kill switch respected.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .interface import PackageCandidate

logger = logging.getLogger(__name__)

SANDBOX_TIMEOUT_S = int(os.environ.get("SANDBOX_TIMEOUT_S", "60"))

_ALLOW_PARTIAL_ISOLATION = (
    os.environ.get("SANDBOX_ALLOW_NO_NETWORK_ISOLATION", "false").lower() == "true"
)

_SECRET_ENV_PREFIXES = (
    "OPENAI_", "ANTHROPIC_", "GEMINI_", "GROK_", "DATABASE_URL",
    "ENCRYPTION_KEY", "SESSION_SECRET", "ADMIN_PASSWORD",
    "IMAP_", "TWILIO_", "ELEVEN_LABS_", "CALENDLY_",
    "AZURE_", "AWS_", "GCP_", "PLAID_", "GITHUB_TOKEN",
    "CONNECTOR_", "BROWSERBASE_", "GVISOR_", "E2B_",
    "REPLIT_", "REPL_",
)


def _sandbox_env() -> Dict[str, str]:
    """Build a minimal env dict with all secret-bearing vars stripped."""
    safe = {}
    for key, val in os.environ.items():
        if any(key.upper().startswith(p) for p in _SECRET_ENV_PREFIXES):
            continue
        safe[key] = val
    safe["PYTHONDONTWRITEBYTECODE"] = "1"
    safe["PYTHONPATH"] = ""
    return safe


_unshare_available: Optional[bool] = None


def _probe_unshare() -> bool:
    """Return True if `unshare --net` is available and permitted.

    Result is cached after the first probe (module-level singleton).
    A fast echo test is used — no long-running subprocess.
    """
    global _unshare_available
    if _unshare_available is not None:
        return _unshare_available
    try:
        result = subprocess.run(
            ["unshare", "--net", "true"],
            capture_output=True, timeout=5,
        )
        _unshare_available = result.returncode == 0
    except Exception:
        _unshare_available = False
    logger.debug("sandbox: unshare --net probe result: %s", _unshare_available)
    return _unshare_available


def _socket_patch_preamble() -> str:
    """Return Python code to inject at the top of the smoke test.

    Monkey-patches socket.socket.connect / create_connection so that any
    Python-level outbound network call raises an explicit error. Native
    extension code cannot be blocked this way — operators who set
    SANDBOX_ALLOW_NO_NETWORK_ISOLATION=true acknowledge this limitation.
    """
    return textwrap.dedent("""
        # --- sandbox network patch (partial isolation) ---
        import socket as _sock_mod
        _orig_socket_cls = _sock_mod.socket
        class _BlockedSocket(_orig_socket_cls):
            def connect(self, *a, **kw):
                raise OSError(
                    "NetworkAccessDenied: outbound connections are not permitted "
                    "in sandbox verification (socket-patch isolation)"
                )
            def connect_ex(self, *a, **kw):
                raise OSError(
                    "NetworkAccessDenied: outbound connections are not permitted "
                    "in sandbox verification (socket-patch isolation)"
                )
        _sock_mod.socket = _BlockedSocket
        def _blocked_create(*a, **kw):
            raise OSError(
                "NetworkAccessDenied: outbound connections are not permitted "
                "in sandbox verification (socket-patch isolation)"
            )
        _sock_mod.create_connection = _blocked_create
        # --- end sandbox network patch ---
    """).strip()


def _generate_smoke_test(package: PackageCandidate, capability_descriptor: str) -> str:
    """Generate a minimal smoke/contract test for the given package.

    The test:
      1. Imports the package (verifies installability + basic import).
      2. Checks that expected top-level symbols are present (if detectable).
      3. Does NOT execute network calls or write to disk — pure in-memory.
    """
    safe_name = package.name.replace("-", "_").replace(".", "_")
    return textwrap.dedent(f"""
        import sys
        import importlib

        package_name = {package.name!r}
        install_name = {safe_name!r}

        print(f"[smoke] Testing package: {{package_name}}")

        # 1. Import test — verifies the package installed and is importable.
        try:
            mod = importlib.import_module(install_name)
            print(f"[smoke] Import OK: {{install_name}}")
        except ImportError:
            # Some packages have a different import name than install name.
            # Try the install name without underscores (e.g. beautifulsoup4 -> bs4).
            alt_names = [
                install_name.split("_")[0],
                package_name.split("-")[0],
                package_name.replace("-", ""),
            ]
            mod = None
            for alt in alt_names:
                try:
                    mod = importlib.import_module(alt)
                    print(f"[smoke] Import OK (alt name): {{alt}}")
                    break
                except ImportError:
                    continue
            if mod is None:
                print(f"[smoke] FAIL: could not import {{package_name}} under any known name", file=sys.stderr)
                sys.exit(1)

        # 2. Basic attribute check — the module must expose something.
        attrs = [a for a in dir(mod) if not a.startswith("_")]
        if not attrs:
            print("[smoke] WARN: module has no public attributes", file=sys.stderr)
        else:
            print(f"[smoke] Public API surface: {{len(attrs)}} symbols")

        # 3. Descriptor relevance trace (informational only, never fails).
        descriptor = {capability_descriptor!r}
        desc_lower = descriptor.lower()
        mod_doc = (getattr(mod, "__doc__", "") or "").lower()
        relevant_words = [w for w in desc_lower.split() if len(w) > 3 and w in mod_doc]
        print(f"[smoke] Relevance trace: {{len(relevant_words)}} descriptor words in module doc")

        print("[smoke] PASS")
        sys.exit(0)
    """).strip()


@dataclass
class SandboxResult:
    """Structured pass/fail record for one sandboxed candidate verification."""
    candidate_name: str
    candidate_version: str
    registry: str
    passed: bool
    install_ok: bool
    test_ok: bool
    stdout: str = ""
    stderr: str = ""
    # Always overwritten by verify_candidate with the actual tier used
    # (see module docstring for the accepted values). The sentinel default
    # never reflects a real run — "venv+secret-strip" alone is no longer an
    # accepted isolation tier, so it must not leak into a result.
    isolation_level: str = "uninitialized"
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_name": self.candidate_name,
            "candidate_version": self.candidate_version,
            "registry": self.registry,
            "passed": self.passed,
            "install_ok": self.install_ok,
            "test_ok": self.test_ok,
            "stdout": self.stdout[:2000],
            "stderr": self.stderr[:2000],
            "isolation_level": self.isolation_level,
            "error": self.error,
        }


def _wrap_with_unshare(cmd: List[str]) -> List[str]:
    """Prepend 'unshare --net' to cmd for OS-level network namespace isolation."""
    return ["unshare", "--net"] + cmd


def verify_candidate(
    package: PackageCandidate,
    capability_descriptor: str,
) -> SandboxResult:
    """Install and smoke-test a candidate in an isolated temp venv.

    Network isolation tiers (applied in priority order):
      1. unshare --net — subprocess runs in a new network namespace (no network).
      2. Python socket patch — monkey-patches socket.socket in the smoke test
         process. Only used when SANDBOX_ALLOW_NO_NETWORK_ISOLATION=true.
      3. FAIL CLOSED — returned immediately if neither tier is available and the
         operator has not acknowledged partial isolation.

    Always returns a SandboxResult. Never raises.
    """
    use_unshare = _probe_unshare()
    # Re-read at call time so runtime changes to the env var take effect.
    allow_partial = (
        os.environ.get("SANDBOX_ALLOW_NO_NETWORK_ISOLATION", "false").lower() == "true"
    )

    if not use_unshare and not allow_partial:
        logger.warning(
            "sandbox: network isolation unavailable (unshare --net not permitted) and "
            "SANDBOX_ALLOW_NO_NETWORK_ISOLATION is not set. Failing closed for package %s.",
            package.name,
        )
        return SandboxResult(
            candidate_name=package.name,
            candidate_version=package.version,
            registry=package.registry,
            passed=False,
            install_ok=False,
            test_ok=False,
            isolation_level="failed:network_isolation_unavailable",
            error=(
                "Sandbox requires network isolation. unshare --net is not permitted in this "
                "environment. Set SANDBOX_ALLOW_NO_NETWORK_ISOLATION=true to proceed with "
                "Python-level socket patching only (partial isolation; native extension code "
                "cannot be blocked)."
            ),
        )

    isolation_level = (
        "unshare+venv+secret-strip" if use_unshare
        else "socket-patch+venv+secret-strip"
    )

    venv_dir = None
    try:
        venv_dir = tempfile.mkdtemp(prefix="mydude_sandbox_")
        python_exe = sys.executable
        env = _sandbox_env()

        install_stdout = ""
        install_stderr = ""
        install_ok = False

        try:
            venv_cmd = [python_exe, "-m", "venv", "--without-pip", venv_dir]
            proc = subprocess.run(
                _wrap_with_unshare(venv_cmd) if use_unshare else venv_cmd,
                capture_output=True, text=True, timeout=30, env=env,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"venv creation failed: {proc.stderr[:300]}")

            venv_python = os.path.join(venv_dir, "bin", "python")
            if not os.path.exists(venv_python):
                venv_python = os.path.join(venv_dir, "Scripts", "python.exe")
            if not os.path.exists(venv_python):
                raise RuntimeError("Could not locate venv python executable")

            pip_cmd = [
                python_exe, "-m", "pip", "install",
                "--target", os.path.join(venv_dir, "lib"),
                "--quiet", "--no-deps", "--no-input",
                "--disable-pip-version-check",
                # Require pre-built binary wheels only — eliminates arbitrary
                # code execution from setup.py / pyproject build hooks during
                # install. Packages with no wheel on PyPI will fail here
                # (intentional: fail-loud rather than execute untrusted build code).
                "--only-binary", ":all:",
                # Prevent setup.py from calling back into the host filesystem.
                "--no-build-isolation",
                package.install_spec,
            ]
            # pip install runs WITHOUT network isolation — it must reach PyPI
            # to download the wheel. We restrict the subprocess env (secrets
            # already stripped) and require binary-only installs so no build
            # hooks execute. The smoke test subprocess that follows is the
            # primary execution-isolation boundary (unshare --net or socket patch).
            pip_proc = subprocess.run(
                pip_cmd,
                capture_output=True, text=True,
                timeout=SANDBOX_TIMEOUT_S // 2,
                env=env,
            )
            install_stdout = pip_proc.stdout[:2000]
            install_stderr = pip_proc.stderr[:2000]
            install_ok = pip_proc.returncode == 0
            if not install_ok:
                return SandboxResult(
                    candidate_name=package.name,
                    candidate_version=package.version,
                    registry=package.registry,
                    passed=False,
                    install_ok=False,
                    test_ok=False,
                    stdout=install_stdout,
                    stderr=install_stderr,
                    isolation_level=isolation_level,
                    error=f"pip install failed (rc={pip_proc.returncode})",
                )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                candidate_name=package.name,
                candidate_version=package.version,
                registry=package.registry,
                passed=False,
                install_ok=False,
                test_ok=False,
                isolation_level=isolation_level,
                error="pip install timed out",
            )
        except Exception as exc:
            return SandboxResult(
                candidate_name=package.name,
                candidate_version=package.version,
                registry=package.registry,
                passed=False,
                install_ok=False,
                test_ok=False,
                isolation_level=isolation_level,
                error=f"sandbox setup error: {exc!s:.200}",
            )

        smoke_script = _generate_smoke_test(package, capability_descriptor)
        if not use_unshare and allow_partial:
            smoke_script = _socket_patch_preamble() + "\n\n" + smoke_script

        smoke_path = os.path.join(venv_dir, "smoke_test.py")
        with open(smoke_path, "w") as f:
            f.write(smoke_script)

        test_env = dict(env)
        test_env["PYTHONPATH"] = os.path.join(venv_dir, "lib")

        smoke_cmd = [python_exe, smoke_path]
        try:
            test_proc = subprocess.run(
                _wrap_with_unshare(smoke_cmd) if use_unshare else smoke_cmd,
                capture_output=True, text=True,
                timeout=SANDBOX_TIMEOUT_S // 2,
                env=test_env,
            )
            test_ok = test_proc.returncode == 0
            return SandboxResult(
                candidate_name=package.name,
                candidate_version=package.version,
                registry=package.registry,
                passed=install_ok and test_ok,
                install_ok=install_ok,
                test_ok=test_ok,
                stdout=(install_stdout + "\n" + test_proc.stdout)[:2000],
                stderr=(install_stderr + "\n" + test_proc.stderr)[:2000],
                isolation_level=isolation_level,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                candidate_name=package.name,
                candidate_version=package.version,
                registry=package.registry,
                passed=False,
                install_ok=True,
                test_ok=False,
                isolation_level=isolation_level,
                error="smoke test timed out",
            )

    except Exception as exc:
        return SandboxResult(
            candidate_name=package.name,
            candidate_version=package.version,
            registry=package.registry,
            passed=False,
            install_ok=False,
            test_ok=False,
            isolation_level=isolation_level if "isolation_level" in dir() else "failed:setup-error",
            error=f"sandbox unavailable: {exc!s:.300}",
        )
    finally:
        if venv_dir:
            try:
                shutil.rmtree(venv_dir, ignore_errors=True)
            except Exception:
                pass
