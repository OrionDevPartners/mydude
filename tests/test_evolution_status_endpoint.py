"""Tests for the lightweight evolution component status endpoint.

The React dashboard polls ``GET /api/evolution/components/{id}/status``
(src/web/api/evolution_routes.py -> ``get_component_status``) every few seconds
for cheap live updates instead of re-fetching the full component detail. That
endpoint is a thin wrapper over ``evolution_store.get_component_status()``, which
returns counters plus a change signature (active-thesis id/status/iteration count
and the latest cycle-log id) so the dashboard can tell when something moved
without parsing any JSON payloads.

This suite locks in that contract so future refactors can't silently break the
dashboard's live polling:
  * the endpoint returns the expected fields (incl. ``thread_alive``) for a known
    component;
  * it 404s for an unknown component id;
  * ``evolution_store.get_component_status()`` reflects active-thesis iteration
    counts and the latest cycle-log id as they change.

We mount the real ``/api`` router on a throwaway FastAPI app — exactly like
tests/test_api_local_models.py — so the TestClient exercises the same handler the
SPA hits. Authentication is satisfied via DEV_AUTH_BYPASS so no login round-trip
or cookie is needed. The store tests run directly against the dev DB using
throwaway component/thesis/iteration/cycle-log rows that are cleaned up
afterwards. No network or LLM calls are made.

Runnable two ways:
  * ``python tests/test_evolution_status_endpoint.py`` (standalone; non-zero on failure)
  * ``pytest tests/test_evolution_status_endpoint.py``  (test_* functions; no plugins needed)
"""
import os
import sys
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.database import SessionLocal
from src.models import (
    CognitionComponent,
    CognitionThesis,
    ThesisTrialIteration,
    EvolutionCycleLog,
)
from src.promptopt import evolution_store as estore
from src.web.api.router import router as api_router

TEST_PREFIX = "__evol_status_test__"

# The flat set of fields the dashboard's live poll relies on. Keep in lockstep
# with evolution_store.get_component_status() + the endpoint's thread_alive add.
EXPECTED_FIELDS = {
    "id",
    "loop_state",
    "cycle_count",
    "last_cycle_at",
    "total_theses",
    "promoted_theses",
    "active_thesis_id",
    "active_thesis_status",
    "active_thesis_iterations",
    "latest_cycle_log_id",
    "thread_alive",
}


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _client() -> TestClient:
    """A TestClient over a minimal app that mounts only the real /api router."""
    app = FastAPI()
    app.include_router(api_router)
    # Don't raise server exceptions so a require_auth 303 surfaces as a response.
    return TestClient(app, raise_server_exceptions=False)


@contextmanager
def _env(**overrides):
    """Temporarily set/unset env vars, restoring the prior state afterwards."""
    saved = {k: os.environ.get(k) for k in overrides}
    try:
        for k, v in overrides.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextmanager
def _bypass_auth():
    """Make require_auth pass without a login round-trip (dev bypass)."""
    with _env(DEV_AUTH_BYPASS="1", REPLIT_DEPLOYMENT=None):
        yield


def _make_component(name: str, ctype: str = "swarm_config") -> int:
    return estore.ensure_component(
        name=name,
        component_type=ctype,
        description="test component",
        truth_json={"source": "test"},
    )


def _cleanup(*names: str) -> None:
    db = SessionLocal()
    try:
        for name in names:
            c = db.query(CognitionComponent).filter_by(name=name).first()
            if c is None:
                continue
            db.query(EvolutionCycleLog).filter_by(component_id=c.id).delete()
            thesis_ids = [t.id for t in db.query(CognitionThesis).filter_by(component_id=c.id).all()]
            for tid in thesis_ids:
                db.query(ThesisTrialIteration).filter_by(thesis_id=tid).delete()
            db.query(CognitionThesis).filter_by(component_id=c.id).delete()
            db.delete(c)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _unknown_component_id() -> int:
    """An id guaranteed not to exist (max id + a wide margin)."""
    db = SessionLocal()
    try:
        from sqlalchemy import func
        max_id = db.query(func.max(CognitionComponent.id)).scalar() or 0
        return int(max_id) + 100000
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Endpoint: shape for a known component
# ---------------------------------------------------------------------------

def test_status_endpoint_returns_expected_fields_for_known_component():
    name = TEST_PREFIX + "endpoint_shape"
    _cleanup(name)
    try:
        component_id = _make_component(name, "swarm_config")
        with _bypass_auth():
            resp = _client().get("/api/evolution/components/%d/status" % component_id)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Exactly the contract the dashboard polls — no missing/extra fields.
        assert set(body.keys()) == EXPECTED_FIELDS, sorted(body.keys())

        # Identity + types the dashboard relies on.
        assert body["id"] == component_id, body
        assert isinstance(body["cycle_count"], int), body
        assert isinstance(body["total_theses"], int), body
        assert isinstance(body["promoted_theses"], int), body
        assert isinstance(body["active_thesis_iterations"], int), body
        assert isinstance(body["thread_alive"], bool), body

        # A fresh component has no theses, no active thesis, no cycle logs.
        assert body["total_theses"] == 0, body
        assert body["promoted_theses"] == 0, body
        assert body["active_thesis_id"] is None, body
        assert body["active_thesis_status"] is None, body
        assert body["active_thesis_iterations"] == 0, body
        assert body["latest_cycle_log_id"] is None, body
        # Not running -> thread_alive False.
        assert body["thread_alive"] is False, body
        print("PASS test_status_endpoint_returns_expected_fields_for_known_component")
    finally:
        _cleanup(name)


# ---------------------------------------------------------------------------
# Endpoint: 404 for an unknown component id
# ---------------------------------------------------------------------------

def test_status_endpoint_404_for_unknown_component():
    unknown = _unknown_component_id()
    with _bypass_auth():
        resp = _client().get("/api/evolution/components/%d/status" % unknown)
    assert resp.status_code == 404, resp.text
    print("PASS test_status_endpoint_404_for_unknown_component")


# ---------------------------------------------------------------------------
# Endpoint: requires auth when the dev bypass is off
# ---------------------------------------------------------------------------

def test_status_endpoint_requires_auth_without_dev_bypass():
    name = TEST_PREFIX + "endpoint_auth"
    _cleanup(name)
    try:
        component_id = _make_component(name, "swarm_config")
        with _env(DEV_AUTH_BYPASS=None, REPLIT_DEPLOYMENT=None):
            resp = _client().get(
                "/api/evolution/components/%d/status" % component_id,
                follow_redirects=False,
            )
        assert resp.status_code in (302, 303, 307, 401), resp.status_code
        assert resp.headers.get("location") == "/login", resp.headers
        print("PASS test_status_endpoint_requires_auth_without_dev_bypass")
    finally:
        _cleanup(name)


# ---------------------------------------------------------------------------
# Store: status reflects active-thesis iteration counts as they change
# ---------------------------------------------------------------------------

def test_status_reflects_active_thesis_iteration_counts():
    name = TEST_PREFIX + "iteration_counts"
    _cleanup(name)
    try:
        component_id = _make_component(name, "swarm_config")

        # No active thesis yet.
        status = estore.get_component_status(component_id)
        assert status is not None, "known component must return a status dict"
        assert status["active_thesis_id"] is None, status
        assert status["active_thesis_iterations"] == 0, status
        assert status["total_theses"] == 0, status

        # Create a thesis (starts in 'proposed', which is an active status).
        thesis_id = estore.create_thesis(
            component_id=component_id,
            branch_cell="evidence_strength",
            thesis={"value": 0.70, "key": "swarm.min_evidence_strength"},
            rationale="status test thesis",
            cycle_index=1,
        )
        status = estore.get_component_status(component_id)
        assert status["active_thesis_id"] == thesis_id, status
        assert status["active_thesis_status"] == "proposed", status
        assert status["active_thesis_iterations"] == 0, status
        assert status["total_theses"] == 1, status

        # Each recorded iteration must bump the active-thesis iteration count.
        for n in range(1, 4):
            estore.record_iteration(
                thesis_id=thesis_id,
                iteration_no=n,
                test_results={"sandbox": "EXPERIMENTAL"},
                compliance_score=70.0,
                hallucination_risk=0.1,
                composite_score=0.65,
                all_tests_passed=True,
                outcome="pass",
            )
            status = estore.get_component_status(component_id)
            assert status["active_thesis_iterations"] == n, (n, status)

        # Once the thesis leaves active statuses, it's no longer the active one.
        estore.update_thesis_status(thesis_id, "rejected")
        status = estore.get_component_status(component_id)
        assert status["active_thesis_id"] is None, status
        assert status["active_thesis_status"] is None, status
        assert status["active_thesis_iterations"] == 0, status
        # ...but it still counts toward the component total.
        assert status["total_theses"] == 1, status
        print("PASS test_status_reflects_active_thesis_iteration_counts")
    finally:
        _cleanup(name)


# ---------------------------------------------------------------------------
# Store: status reflects the latest cycle-log id as logs are added
# ---------------------------------------------------------------------------

def test_status_reflects_latest_cycle_log_id():
    name = TEST_PREFIX + "latest_cycle_log"
    _cleanup(name)
    try:
        component_id = _make_component(name, "swarm_config")

        # No cycle logs yet.
        status = estore.get_component_status(component_id)
        assert status["latest_cycle_log_id"] is None, status
        assert status["cycle_count"] == 0, status

        # First cycle log becomes the latest.
        first_log_id = estore.log_cycle(
            component_id=component_id,
            cycle_index=1,
            outcome="rejected",
            thesis_id=None,
            next_selection={"next_cycle": 2},
            detail="first cycle",
        )
        cycle_count = estore.increment_cycle(component_id)
        status = estore.get_component_status(component_id)
        assert status["latest_cycle_log_id"] == first_log_id, status
        assert status["cycle_count"] == cycle_count, status

        # A newer cycle log supersedes it as the change signature advances.
        second_log_id = estore.log_cycle(
            component_id=component_id,
            cycle_index=2,
            outcome="promoted",
            thesis_id=None,
            next_selection={"next_cycle": 3},
            detail="second cycle",
        )
        assert second_log_id != first_log_id, (first_log_id, second_log_id)
        status = estore.get_component_status(component_id)
        assert status["latest_cycle_log_id"] == second_log_id, status
        print("PASS test_status_reflects_latest_cycle_log_id")
    finally:
        _cleanup(name)


# ---------------------------------------------------------------------------
# Store: unknown component id returns None (the endpoint's 404 source)
# ---------------------------------------------------------------------------

def test_get_component_status_none_for_unknown_component():
    unknown = _unknown_component_id()
    assert estore.get_component_status(unknown) is None
    print("PASS test_get_component_status_none_for_unknown_component")


# ---------------------------------------------------------------------------
# Store: promoted thesis is counted in promoted_theses
# ---------------------------------------------------------------------------

def test_status_counts_promoted_theses():
    name = TEST_PREFIX + "promoted_count"
    _cleanup(name)
    try:
        component_id = _make_component(name, "swarm_config")
        thesis_id = estore.create_thesis(
            component_id=component_id,
            branch_cell="evidence_strength",
            thesis={"value": 0.70, "key": "swarm.min_evidence_strength"},
            rationale="promote me",
            cycle_index=1,
        )
        status = estore.get_component_status(component_id)
        assert status["promoted_theses"] == 0, status

        estore.update_thesis_status(thesis_id, "promoted")
        status = estore.get_component_status(component_id)
        assert status["promoted_theses"] == 1, status
        assert status["total_theses"] == 1, status
        # A promoted thesis is not an active one.
        assert status["active_thesis_id"] is None, status
        print("PASS test_status_counts_promoted_theses")
    finally:
        _cleanup(name)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
        except Exception as e:
            failed += 1
            print("FAIL %s: %s: %s" % (fn.__name__, type(e).__name__, e))
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
