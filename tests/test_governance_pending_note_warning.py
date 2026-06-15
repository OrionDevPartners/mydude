"""Tests for the "no real setting changed" warning on enacted proposals.

Regression coverage for the pending-note path in
``src/swarm/governance_engine.py`` (``_apply_enacted_action``) and the
``/api/governance`` serializer in ``src/web/api/router.py``.

When an enacted proposal's free-text action matches no known tuning keyword, the
engine records it as a pending operator note (``swarm.pending_action.<id>``)
instead of changing any live swarm setting. Operators must be warned that the
proposal looked applied but actually tuned nothing.

These tests pin:
  * ``enactment_is_no_op`` classifies pending-note-only applied_settings as a
    no-op, but NOT real changes, mixed lists, or an empty (failed-apply) list;
  * an operator-enacted proposal whose wording maps to no action is recorded as
    a pending note (live engine path);
  * the ``/api/governance`` response flags that proposal with
    ``no_op_enactment=True`` while a real mapped change stays ``False``.

DB-backed; runs against the real dev database and cleans up its own rows.

Runnable two ways:
  * python tests/test_governance_pending_note_warning.py
  * pytest tests/test_governance_pending_note_warning.py
"""
import atexit
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database import SessionLocal
from src.models import (
    AppSetting,
    GovernanceEnactment,
    GovernanceProposal,
    GovernanceVote,
)
from src.swarm.governance_engine import (
    GovernanceEngine,
    PENDING_ACTION_PREFIX,
    enactment_is_no_op,
)

_CREATED_PROPOSAL_DB_IDS = []
_TOUCHED_SETTING_KEYS = ["swarm.min_cs_threshold"]
_PENDING_KEYS_TO_DELETE = []


def _raise(track, proposed_action, *, title="pending-note-test", origin="operator"):
    pid_str = GovernanceEngine().raise_proposal(
        origin=origin,
        track=track,
        title=title,
        description="pending-note regression test",
        proposed_action=proposed_action,
        evidence=["test-evidence"],
    )
    assert pid_str, "raise_proposal returned None"
    db = SessionLocal()
    try:
        prop = db.query(GovernanceProposal).filter_by(proposal_id=pid_str).first()
        assert prop is not None
        dbid = prop.id
        _PENDING_KEYS_TO_DELETE.append(f"{PENDING_ACTION_PREFIX}{prop.proposal_id}")
    finally:
        db.close()
    _CREATED_PROPOSAL_DB_IDS.append(dbid)
    return dbid


def _del_setting(key):
    db = SessionLocal()
    try:
        db.query(AppSetting).filter_by(key=key).delete()
        db.commit()
    finally:
        db.close()


def _cleanup():
    db = SessionLocal()
    try:
        for dbid in _CREATED_PROPOSAL_DB_IDS:
            db.query(GovernanceVote).filter_by(proposal_id=dbid).delete()
            db.query(GovernanceEnactment).filter_by(proposal_id=dbid).delete()
            db.query(GovernanceProposal).filter_by(id=dbid).delete()
        for key in _PENDING_KEYS_TO_DELETE:
            db.query(AppSetting).filter_by(key=key).delete()
        for key in _TOUCHED_SETTING_KEYS:
            db.query(AppSetting).filter_by(key=key).delete()
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
    _CREATED_PROPOSAL_DB_IDS.clear()
    _PENDING_KEYS_TO_DELETE.clear()


atexit.register(_cleanup)


# --------------------------------------------------------------------------- #
# Pure classifier
# --------------------------------------------------------------------------- #

def test_enactment_is_no_op_classifier():
    assert enactment_is_no_op([f"{PENDING_ACTION_PREFIX}GP-1=<pending_note>"]) is True
    assert enactment_is_no_op(["swarm.min_cs_threshold=50"]) is False
    # A mix of a real change + a pending note is NOT a no-op (something changed).
    assert enactment_is_no_op([
        f"{PENDING_ACTION_PREFIX}GP-1=<pending_note>", "swarm.max_concurrency=4",
    ]) is False
    # Empty list = the apply step failed outright; not classified as a pending note.
    assert enactment_is_no_op([]) is False
    assert enactment_is_no_op(None) is False


# --------------------------------------------------------------------------- #
# Live engine path: unmapped wording -> pending note
# --------------------------------------------------------------------------- #

def test_unmapped_action_records_pending_note():
    dbid = _raise("tuning", "please make the swarm generally smarter somehow")
    eng = GovernanceEngine()
    assert eng.operator_enact(dbid, operator="tester") is True

    db = SessionLocal()
    try:
        rows = db.query(GovernanceEnactment).filter_by(proposal_id=dbid).all()
        assert len(rows) == 1
        applied = json.loads(rows[0].change_json or "{}").get("applied_settings")
    finally:
        db.close()

    assert applied and all(s.startswith(PENDING_ACTION_PREFIX) for s in applied), \
        "unmapped wording must be recorded as a pending note only"
    assert enactment_is_no_op(applied) is True


def test_mapped_action_is_not_a_no_op():
    _del_setting("swarm.min_cs_threshold")
    dbid = _raise("tuning", "compliance correction required after cs degradation")
    eng = GovernanceEngine()
    assert eng.operator_enact(dbid, operator="tester") is True

    db = SessionLocal()
    try:
        rows = db.query(GovernanceEnactment).filter_by(proposal_id=dbid).all()
        applied = json.loads(rows[0].change_json or "{}").get("applied_settings")
    finally:
        db.close()

    assert "swarm.min_cs_threshold=50" in applied
    assert enactment_is_no_op(applied) is False


# --------------------------------------------------------------------------- #
# API serializer flags the no-op enactment
# --------------------------------------------------------------------------- #

def test_api_governance_flags_no_op_enactment():
    from fastapi.testclient import TestClient
    from src.web.app import app

    noop_id = _raise("tuning", "please make the swarm generally smarter somehow",
                     title="pending-note-noop")
    real_id = _raise("tuning", "compliance correction required after cs degradation",
                     title="pending-note-real")
    eng = GovernanceEngine()
    assert eng.operator_enact(noop_id, operator="tester") is True
    assert eng.operator_enact(real_id, operator="tester") is True

    # Dev-only auth bypass (never enabled in a deployment) so require_auth passes
    # without seeding a user; mirrors the dev workspace env.
    os.environ["DEV_AUTH_BYPASS"] = "1"
    os.environ.pop("REPLIT_DEPLOYMENT", None)
    client = TestClient(app)  # no `with` -> startup lifespan never runs
    resp = client.get("/api/governance")
    assert resp.status_code == 200, resp.text
    recent = resp.json().get("recent_proposals", [])
    by_id = {p["id"]: p for p in recent}

    assert noop_id in by_id, "enacted no-op proposal missing from recent_proposals"
    assert real_id in by_id, "enacted real-change proposal missing from recent_proposals"
    assert by_id[noop_id]["no_op_enactment"] is True, \
        "pending-note-only enactment must be flagged no_op_enactment=True"
    assert by_id[real_id]["no_op_enactment"] is False, \
        "a real mapped setting change must NOT be flagged as a no-op"


_TESTS = [
    test_enactment_is_no_op_classifier,
    test_unmapped_action_records_pending_note,
    test_mapped_action_is_not_a_no_op,
    test_api_governance_flags_no_op_enactment,
]


def main():
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
