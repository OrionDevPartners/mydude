"""Tests for sandbox network-isolation tier selection.

Coverage (the network-blocking upgrade in src/acquisition/sandbox.py):
  - FAIL CLOSED when unshare --net is unavailable and the operator has not
    opted into partial isolation (the default Replit posture).
  - isolation_level reflects the *actual* tier used:
      unshare available           -> "unshare+venv+secret-strip"
      socket-patch opt-in (no ns) -> "socket-patch+venv+secret-strip"
  - The smoke-test subprocess is wrapped with `unshare --net` only when the
    namespace tier is active; the pip install is never namespace-wrapped
    (it must reach the registry to download the wheel).
  - The socket-patch preamble actually blocks outbound connections.
  - Production secrets are stripped from the sandbox env.

These tests are hermetic: no real pip install or network access is performed
(subprocess.run is mocked for the tier-selection cases).
"""
from __future__ import annotations

import os
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.acquisition.interface import PackageCandidate
from src.acquisition import sandbox


def _pkg() -> PackageCandidate:
    return PackageCandidate(
        name="six", version="1.16.0", registry="pypi", install_spec="six==1.16.0"
    )


class _FakeRun:
    """Stand-in for subprocess.run that records commands and always succeeds."""

    def __init__(self):
        self.commands = []
        self.smoke_script = None

    def __call__(self, cmd, *args, **kwargs):
        self.commands.append(list(cmd))
        # Capture the on-disk smoke script before the temp dir is torn down.
        for token in cmd:
            if str(token).endswith("smoke_test.py"):
                try:
                    with open(str(token)) as f:
                        self.smoke_script = f.read()
                except OSError:
                    pass
        return SimpleNamespace(returncode=0, stdout="[smoke] PASS", stderr="")


class FailClosedTests(unittest.TestCase):
    def test_fails_closed_without_isolation(self):
        """No namespace + no opt-in => deny, never touch the network."""
        with patch.object(sandbox, "_probe_unshare", return_value=False), \
                patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SANDBOX_ALLOW_NO_NETWORK_ISOLATION", None)
            # If anything tries to spawn a subprocess we want to know about it.
            with patch.object(sandbox.subprocess, "run",
                              side_effect=AssertionError("subprocess spawned on fail-closed path")):
                result = sandbox.verify_candidate(_pkg(), "compatibility shim")

        self.assertFalse(result.passed)
        self.assertFalse(result.install_ok)
        self.assertEqual(result.isolation_level, "failed:network_isolation_unavailable")
        self.assertIn("network isolation", (result.error or "").lower())


class IsolationTierSelectionTests(unittest.TestCase):
    def _run(self, *, unshare: bool, allow_partial: bool):
        fake = _FakeRun()
        env = {"SANDBOX_ALLOW_NO_NETWORK_ISOLATION": "true"} if allow_partial else {}
        with patch.object(sandbox, "_probe_unshare", return_value=unshare), \
                patch.object(sandbox.subprocess, "run", fake), \
                patch.object(sandbox.os.path, "exists", return_value=True), \
                patch.dict(os.environ, env, clear=False):
            if not allow_partial:
                os.environ.pop("SANDBOX_ALLOW_NO_NETWORK_ISOLATION", None)
            result = sandbox.verify_candidate(_pkg(), "compatibility shim")
        return result, fake

    def _smoke_and_pip_cmds(self, fake: _FakeRun):
        smoke = next(c for c in fake.commands if any(str(x).endswith("smoke_test.py") for x in c))
        pip = next(c for c in fake.commands if "pip" in c)
        return smoke, pip

    def test_unshare_tier(self):
        result, fake = self._run(unshare=True, allow_partial=False)
        self.assertTrue(result.passed)
        self.assertEqual(result.isolation_level, "unshare+venv+secret-strip")
        smoke, pip = self._smoke_and_pip_cmds(fake)
        # Smoke test runs inside the network namespace ...
        self.assertEqual(smoke[:2], ["unshare", "--net"])
        # ... but pip install must NOT (it needs the registry).
        self.assertNotEqual(pip[:2], ["unshare", "--net"])

    def test_socket_patch_tier(self):
        result, fake = self._run(unshare=False, allow_partial=True)
        self.assertTrue(result.passed)
        self.assertEqual(result.isolation_level, "socket-patch+venv+secret-strip")
        smoke, _ = self._smoke_and_pip_cmds(fake)
        # No namespace available -> smoke test is not unshare-wrapped.
        self.assertNotEqual(smoke[:2], ["unshare", "--net"])
        # The smoke script carries the socket-patch preamble.
        self.assertIsNotNone(fake.smoke_script)
        self.assertIn("NetworkAccessDenied", fake.smoke_script)


class SocketPatchPreambleTests(unittest.TestCase):
    def test_preamble_blocks_outbound_connections(self):
        """The injected preamble must make socket.connect raise."""
        ns: dict = {}
        exec(sandbox._socket_patch_preamble(), ns)  # noqa: S102 - trusted internal code
        import socket as real_socket

        s = ns["_sock_mod"].socket(real_socket.AF_INET, real_socket.SOCK_STREAM)
        try:
            with self.assertRaises(OSError) as ctx:
                s.connect(("example.com", 80))
            self.assertIn("NetworkAccessDenied", str(ctx.exception))
        finally:
            s.close()

        with self.assertRaises(OSError):
            ns["_sock_mod"].create_connection(("example.com", 80))


class SecretStrippingTests(unittest.TestCase):
    def test_secret_env_vars_are_stripped(self):
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "sk-secret",
            "DATABASE_URL": "postgres://secret",
            "ENCRYPTION_KEY": "fernet-secret",
            "SAFE_VAR": "keep-me",
        }, clear=False):
            env = sandbox._sandbox_env()
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("DATABASE_URL", env)
        self.assertNotIn("ENCRYPTION_KEY", env)
        self.assertEqual(env.get("SAFE_VAR"), "keep-me")
        self.assertEqual(env.get("PYTHONPATH"), "")


if __name__ == "__main__":
    unittest.main()
