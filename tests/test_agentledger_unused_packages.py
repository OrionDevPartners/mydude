"""Tests for the Agent Ledger's "declared but unused" package detection.

The JS/TS import scan only walks ``frontend/src``, so several declared node deps
report "used in 0 containers". This is ambiguous: some are genuinely unused
(safe to remove), while others are wired in only through build/config files
(vite.config.ts, eslint.config.js, *.css @import). The seeder now records the
latter as ``config dependency`` placements so the two cases are distinguishable,
and ``query.packages ... --unused`` classifies every zero-source-import package.

These tests prove, against the REAL repo state:
  * a package imported only from a config file is tagged "config-only", not unused
  * a declared dependency that nothing imports is tagged "DECLARED BUT UNUSED"
  * a package imported from frontend/src never appears in the unused list
  * config-only references are tracked separately from real source imports

Hermetic: the ledger is pointed at a throwaway SQLite file via AGENT_LEDGER_URL
BEFORE importing agentledger (the engine binds at import), so the real
agent_ledger.db is never touched. seed() reads only the repo's manifests/source
(no network, no app database).

Runnable two ways:
  * python tests/test_agentledger_unused_packages.py
  * pytest tests/test_agentledger_unused_packages.py
"""
import atexit
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Isolate to a throwaway DB BEFORE importing agentledger (engine binds at import).
_TMP_DIR = tempfile.mkdtemp(prefix="agentledger_unused_test_")
_TMP_DB = os.path.join(_TMP_DIR, "ledger.db")
os.environ["AGENT_LEDGER_URL"] = f"sqlite:///{_TMP_DB}"

from agentledger import query  # noqa: E402
from agentledger.db import SessionLocal, init_ledger, engine  # noqa: E402
from agentledger.models import Package, Placement  # noqa: E402
from agentledger.seed import seed  # noqa: E402


def _cleanup():
    try:
        engine.dispose()
    except Exception:
        pass
    shutil.rmtree(_TMP_DIR, ignore_errors=True)


atexit.register(_cleanup)


def _usage(name: str):
    """(real, config) placement counts for a node package by name."""
    db = SessionLocal()
    try:
        pkg = db.query(Package).filter(
            Package.name == name, Package.ecosystem == "node").first()
        assert pkg is not None, f"package '{name}' not declared in frontend manifest"
        return query._package_usage(db, pkg.id)
    finally:
        db.close()


def test_config_only_package_is_not_flagged_unused():
    """tailwindcss is referenced only via `@import` in index.css -> config-only."""
    seed()
    real, config = _usage("tailwindcss")
    assert real == 0, f"tailwindcss should have no source imports, got {real}"
    assert config >= 1, f"tailwindcss should have a config placement, got {config}"

    db = SessionLocal()
    try:
        pkg = db.query(Package).filter(
            Package.name == "tailwindcss", Package.ecosystem == "node").first()
        verdict = query._unused_classification(pkg, config)
    finally:
        db.close()
    assert "config-only" in verdict, f"expected config-only verdict, got: {verdict}"
    assert "UNUSED" not in verdict.upper(), f"config dep wrongly flagged unused: {verdict}"


def test_vite_plugin_resolved_from_config_file():
    """A vite.config.ts import (@vitejs/plugin-react) is captured as config-only."""
    seed()
    real, config = _usage("@vitejs/plugin-react")
    assert real == 0 and config >= 1, (
        f"@vitejs/plugin-react should be config-only, got real={real} config={config}")


def test_declared_unused_dependency_is_flagged():
    """clsx is declared but imported nowhere (src or config) -> review-for-removal."""
    seed()
    real, config = _usage("clsx")
    assert real == 0 and config == 0, (
        f"clsx should have zero placements, got real={real} config={config}")

    db = SessionLocal()
    try:
        pkg = db.query(Package).filter(
            Package.name == "clsx", Package.ecosystem == "node").first()
        verdict = query._unused_classification(pkg, config)
    finally:
        db.close()
    assert "UNUSED" in verdict.upper(), f"expected unused verdict, got: {verdict}"


def test_source_imported_package_excluded_from_unused():
    """react-router-dom is imported from frontend/src -> real usage, never unused."""
    seed()
    real, config = _usage("react-router-dom")
    assert real >= 1, f"react-router-dom should have real source imports, got {real}"


def test_radix_tooltip_used_but_siblings_unused():
    """Only @radix-ui/react-tooltip is imported in src; its siblings are unused.

    Proves the distinction is per-package (not a blanket @radix-ui verdict)."""
    seed()
    real_tooltip, _ = _usage("@radix-ui/react-tooltip")
    real_dialog, cfg_dialog = _usage("@radix-ui/react-dialog")
    assert real_tooltip >= 1, "react-tooltip is imported in src and must show real usage"
    assert real_dialog == 0 and cfg_dialog == 0, (
        "react-dialog is imported nowhere and must be flagged unused")


def test_config_placements_kept_separate_from_real_imports():
    """No `config dependency` placement is ever counted as a real import."""
    seed()
    db = SessionLocal()
    try:
        cfg_count = db.query(Placement).filter(
            Placement.subject_kind == "package",
            Placement.role == "config dependency").count()
        # The frontend wires tailwindcss + several build plugins via config files.
        assert cfg_count >= 1, "expected at least one config-only placement"
        # _package_usage must never mix the two buckets for any package.
        for pkg in db.query(Package).filter(Package.ecosystem == "node").all():
            real, config = query._package_usage(db, pkg.id)
            total = db.query(Placement).filter(
                Placement.subject_kind == "package",
                Placement.subject_id == pkg.id).count()
            assert real + config == total, (
                f"{pkg.name}: real({real})+config({config}) != total placements({total})")
    finally:
        db.close()


_TESTS = [
    test_config_only_package_is_not_flagged_unused,
    test_vite_plugin_resolved_from_config_file,
    test_declared_unused_dependency_is_flagged,
    test_source_imported_package_excluded_from_unused,
    test_radix_tooltip_used_but_siblings_unused,
    test_config_placements_kept_separate_from_real_imports,
]


def main():
    init_ledger(drop=True)
    failures = []
    try:
        for t in _TESTS:
            try:
                t()
                print("PASS", t.__name__, flush=True)
            except Exception as e:
                import traceback
                print("FAIL", t.__name__, "->", e, flush=True)
                traceback.print_exc()
                failures.append(t.__name__)
    finally:
        _cleanup()
    print("\n%d passed, %d failed" % (len(_TESTS) - len(failures), len(failures)), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
