"""Tests for the acquisition loop learning from past rejections.

The acquisition orchestrator (``src/acquisition/orchestrator.py``) must not
re-fetch / re-evaluate packages it already judged. These tests pin the dedup
core:

  1. Config window (``_dedup_days``): reads ``ACQUISITION_DEDUP_DAYS``, defaults
     to 7, and ignores garbage / non-positive values.
  2. History lookup (``_candidate_history``): scoped to the capability via the
     parent job, windowed by ``created_at``, case/whitespace-insensitive on the
     package name, with "approved" winning over "rejected".
     - A candidate that failed governance within the window -> "rejected".
     - A candidate that passed governance within the window -> "approved".
     - A candidate that failed but is OLDER than the window -> not returned
       (so a retry is allowed once the cooldown elapses).
     - A rejection for a DIFFERENT capability never leaks across.

They are fully hermetic: each test runs against a fresh in-memory SQLite
database (the models use only portable column types) — no network, no shared-DB
interference, no live credentials.

Runnable two ways:
  * ``python tests/test_acquisition_dedup.py``  (standalone, non-zero on failure)
  * ``pytest tests/test_acquisition_dedup.py``   (test_* functions; no plugins)
"""
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database import Base
from src import models  # noqa: F401  (registers all tables on Base.metadata)
from src.models import CapabilityAcquisitionJob, AcquisitionCandidate
from src.acquisition import orchestrator


# -- helpers -----------------------------------------------------------------

@contextmanager
def _patch(obj, name, value):
    missing = object()
    orig = getattr(obj, name, missing)
    setattr(obj, name, value)
    try:
        yield
    finally:
        if orig is missing:
            delattr(obj, name)
        else:
            setattr(obj, name, orig)


@contextmanager
def _env(name, value):
    missing = object()
    orig = os.environ.get(name, missing)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    try:
        yield
    finally:
        if orig is missing:
            os.environ.pop(name, None)
        else:
            os.environ[name] = orig


def _make_db():
    """A fresh, isolated in-memory SQLite sessionmaker with all tables created."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def _seed(SessionLocal, capability, candidate_name, *, passed_governance,
          passed_sandbox=True, age_days=0):
    """Create a job for ``capability`` plus one candidate row under it."""
    db = SessionLocal()
    try:
        job = CapabilityAcquisitionJob(
            job_id=f"ACQ-{candidate_name[:8]}-{age_days}",
            capability=capability,
            state="rejected" if not passed_governance else "approved",
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        cand = AcquisitionCandidate(
            job_id=job.id,
            candidate_name=candidate_name,
            candidate_version="1.0.0",
            registry="pypi",
            passed_sandbox=passed_sandbox,
            passed_governance=passed_governance,
            created_at=datetime.utcnow() - timedelta(days=age_days),
        )
        db.add(cand)
        db.commit()
    finally:
        db.close()


# -- _dedup_days -------------------------------------------------------------

def test_dedup_days_default():
    with _env("ACQUISITION_DEDUP_DAYS", None):
        assert orchestrator._dedup_days() == 7


def test_dedup_days_custom():
    with _env("ACQUISITION_DEDUP_DAYS", "14"):
        assert orchestrator._dedup_days() == 14


def test_dedup_days_rejects_garbage_and_nonpositive():
    for bad in ("0", "-3", "abc", "  "):
        with _env("ACQUISITION_DEDUP_DAYS", bad):
            assert orchestrator._dedup_days() == 7, bad


# -- _candidate_history ------------------------------------------------------

def test_recent_rejection_is_skipped():
    SessionLocal = _make_db()
    _seed(SessionLocal, "web_scrape", "shady-pkg", passed_governance=False, age_days=1)
    with _patch(__import__("src.database", fromlist=["x"]), "SessionLocal", SessionLocal):
        hist = orchestrator._candidate_history("web_scrape", 7)
    assert hist == {"shady-pkg": "rejected"}


def test_recent_approval_is_warned():
    SessionLocal = _make_db()
    _seed(SessionLocal, "web_scrape", "good-pkg", passed_governance=True, age_days=2)
    with _patch(__import__("src.database", fromlist=["x"]), "SessionLocal", SessionLocal):
        hist = orchestrator._candidate_history("web_scrape", 7)
    assert hist == {"good-pkg": "approved"}


def test_old_rejection_is_not_deduped():
    SessionLocal = _make_db()
    _seed(SessionLocal, "web_scrape", "stale-pkg", passed_governance=False, age_days=30)
    with _patch(__import__("src.database", fromlist=["x"]), "SessionLocal", SessionLocal):
        hist = orchestrator._candidate_history("web_scrape", 7)
    assert hist == {}


def test_approved_wins_over_rejected():
    SessionLocal = _make_db()
    # Same package failed once, later passed — approval must win.
    _seed(SessionLocal, "web_scrape", "fixed-pkg", passed_governance=False, age_days=3)
    _seed(SessionLocal, "web_scrape", "FIXED-PKG", passed_governance=True, age_days=1)
    with _patch(__import__("src.database", fromlist=["x"]), "SessionLocal", SessionLocal):
        hist = orchestrator._candidate_history("web_scrape", 7)
    # name is normalized to lower-case; approved wins.
    assert hist == {"fixed-pkg": "approved"}


def test_capability_scoping_no_crosstalk():
    SessionLocal = _make_db()
    _seed(SessionLocal, "web_scrape", "pkg-a", passed_governance=False, age_days=1)
    with _patch(__import__("src.database", fromlist=["x"]), "SessionLocal", SessionLocal):
        hist = orchestrator._candidate_history("send_email", 7)
    assert hist == {}


def test_history_never_raises_on_db_error():
    # No patched SessionLocal pointing at a real DB; force an error path by
    # patching SessionLocal to a callable that raises.
    def _boom():
        raise RuntimeError("db down")

    with _patch(__import__("src.database", fromlist=["x"]), "SessionLocal", _boom):
        hist = orchestrator._candidate_history("web_scrape", 7)
    assert hist == {}


# -- standalone runner -------------------------------------------------------

if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {exc!r}")
    if failures:
        print(f"\n{failures} test(s) failed")
        sys.exit(1)
    print("\nall acquisition dedup tests passed")
