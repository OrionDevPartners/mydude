"""Tests for the configurable minimum participation floor (governance engine).

A single unanimous vote must not be able to instantly auto-enact OR auto-reject a
governance proposal. ``GovernanceEngine`` enforces a configurable floor (distinct
voters + total vote weight), read live from the environment, BEFORE the quorum
ratio check in ``_maybe_enact``. This pins:

  * pure floor resolution — defaults, global + per-track env overrides, malformed
    values fall back to the default, negatives clamp to 0 (= dimension disabled);
  * participation_status math — voters/weight met flags + progress, and that an
    abstain counts as participation but never as an effective yes/no vote;
  * the live cast_vote path — one yes is held open, a second yes enacts; one no is
    held open (no auto-reject) until the floor is met; a floor of 1 restores the
    legacy single-vote enact.

DB-backed like test_governance_tuning: runs against the dev DB and cleans up its
own rows + restores any touched swarm.* AppSetting and GOVERNANCE_* env vars.

Runnable two ways:
  * python tests/test_governance_participation_floor.py   (standalone; non-zero exit on failure)
  * pytest tests/test_governance_participation_floor.py     (test_* functions; no plugins needed)
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
from src.swarm.governance_engine import GovernanceEngine, DEFAULT_MIN_VOTERS

# AppSetting keys the enact path may write; snapshotted + restored at teardown so
# we never leave (or clobber) live tuning settings.
_TOUCHED_SETTING_KEYS = ["swarm.min_cs_threshold"]
# GOVERNANCE_* env vars the suite toggles; snapshotted + restored at teardown.
_ENV_KEYS = [
    "GOVERNANCE_MIN_VOTERS",
    "GOVERNANCE_MIN_WEIGHT",
    "GOVERNANCE_MIN_VOTERS_TUNING",
    "GOVERNANCE_MIN_WEIGHT_TUNING",
    "GOVERNANCE_MIN_VOTERS_POLICY",
    "GOVERNANCE_MIN_WEIGHT_POLICY",
]

_CREATED_PROPOSAL_DB_IDS = []
_SETTINGS_SNAPSHOT = {}
_ENV_SNAPSHOT = {}
_SNAPSHOT_TAKEN = False


# --------------------------------------------------------------------------- #
# DB / env helpers
# --------------------------------------------------------------------------- #

def _raise(track, proposed_action, *, title="floor-test", origin="operator"):
    pid_str = GovernanceEngine().raise_proposal(
        origin=origin,
        track=track,
        title=title,
        description="participation floor regression test",
        proposed_action=proposed_action,
        evidence=["test-evidence"],
    )
    assert pid_str, "raise_proposal returned None"
    db = SessionLocal()
    try:
        prop = db.query(GovernanceProposal).filter_by(proposal_id=pid_str).first()
        assert prop is not None, "raised proposal not found in DB"
        dbid = prop.id
    finally:
        db.close()
    _CREATED_PROPOSAL_DB_IDS.append(dbid)
    return dbid


def _prop_status(dbid):
    db = SessionLocal()
    try:
        p = db.query(GovernanceProposal).filter_by(id=dbid).first()
        return None if p is None else p.status
    finally:
        db.close()


def _setting(key):
    db = SessionLocal()
    try:
        s = db.query(AppSetting).filter_by(key=key).first()
        return None if s is None else s.value
    finally:
        db.close()


def _set_setting(key, value):
    db = SessionLocal()
    try:
        s = db.query(AppSetting).filter_by(key=key).first()
        if s:
            s.value = value
        else:
            db.add(AppSetting(key=key, value=value))
        db.commit()
    finally:
        db.close()


def _del_setting(key):
    db = SessionLocal()
    try:
        db.query(AppSetting).filter_by(key=key).delete()
        db.commit()
    finally:
        db.close()


def _enactment_changes(dbid):
    db = SessionLocal()
    try:
        rows = db.query(GovernanceEnactment).filter_by(proposal_id=dbid).all()
        return [json.loads(r.change_json or "{}") for r in rows]
    finally:
        db.close()


def _clear_env():
    """Remove every GOVERNANCE_* override so a test sees pristine defaults."""
    for key in _ENV_KEYS:
        os.environ.pop(key, None)


# --------------------------------------------------------------------------- #
# Snapshot / cleanup
# --------------------------------------------------------------------------- #

def _snapshot():
    global _SNAPSHOT_TAKEN
    if _SNAPSHOT_TAKEN:
        return
    for key in _TOUCHED_SETTING_KEYS:
        _SETTINGS_SNAPSHOT[key] = _setting(key)
    for key in _ENV_KEYS:
        _ENV_SNAPSHOT[key] = os.environ.get(key)
    _SNAPSHOT_TAKEN = True


def _cleanup():
    db = SessionLocal()
    try:
        for dbid in _CREATED_PROPOSAL_DB_IDS:
            db.query(GovernanceVote).filter_by(proposal_id=dbid).delete()
            db.query(GovernanceEnactment).filter_by(proposal_id=dbid).delete()
            db.query(GovernanceProposal).filter_by(id=dbid).delete()
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
    _CREATED_PROPOSAL_DB_IDS.clear()

    if _SNAPSHOT_TAKEN:
        for key, original in _SETTINGS_SNAPSHOT.items():
            if original is None:
                _del_setting(key)
            else:
                _set_setting(key, original)
        for key, original in _ENV_SNAPSHOT.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original


_snapshot()
atexit.register(_cleanup)


# --------------------------------------------------------------------------- #
# Pure floor resolution (no DB)
# --------------------------------------------------------------------------- #

def test_participation_floor_defaults():
    _clear_env()
    floor = GovernanceEngine().participation_floor("tuning")
    assert floor["min_voters"] == DEFAULT_MIN_VOTERS
    assert floor["min_weight"] == 0.0


def test_participation_floor_global_and_per_track_overrides():
    _clear_env()
    try:
        os.environ["GOVERNANCE_MIN_VOTERS"] = "3"
        os.environ["GOVERNANCE_MIN_WEIGHT"] = "2.5"
        eng = GovernanceEngine()
        assert eng.participation_floor("tuning") == {"min_voters": 3, "min_weight": 2.5}
        # A per-track override wins over the global setting for its own track only.
        os.environ["GOVERNANCE_MIN_VOTERS_POLICY"] = "5"
        assert eng.participation_floor("policy")["min_voters"] == 5
        assert eng.participation_floor("tuning")["min_voters"] == 3
    finally:
        _clear_env()


def test_participation_floor_malformed_value_falls_back():
    _clear_env()
    try:
        os.environ["GOVERNANCE_MIN_VOTERS"] = "not-a-number"
        assert GovernanceEngine().participation_floor("tuning")["min_voters"] == DEFAULT_MIN_VOTERS
    finally:
        _clear_env()


def test_participation_floor_clamps_negative():
    _clear_env()
    try:
        os.environ["GOVERNANCE_MIN_VOTERS"] = "-4"
        os.environ["GOVERNANCE_MIN_WEIGHT"] = "-1.0"
        floor = GovernanceEngine().participation_floor("tuning")
        assert floor["min_voters"] == 0
        assert floor["min_weight"] == 0.0
    finally:
        _clear_env()


def test_participation_status_voters_and_progress():
    _clear_env()
    try:
        os.environ["GOVERNANCE_MIN_VOTERS"] = "2"
        os.environ["GOVERNANCE_MIN_WEIGHT"] = "0"
        eng = GovernanceEngine()
        below = eng.participation_status({"vote_count": 1, "participation_weight": 1.0}, "tuning")
        assert below["participation_met"] is False
        assert below["voters_met"] is False
        assert below["voters_progress"] == 0.5

        met = eng.participation_status({"vote_count": 2, "participation_weight": 2.0}, "tuning")
        assert met["participation_met"] is True
        assert met["voters_progress"] == 1.0
    finally:
        _clear_env()


def test_participation_status_weight_dimension_gates_independently():
    _clear_env()
    try:
        os.environ["GOVERNANCE_MIN_VOTERS"] = "1"
        os.environ["GOVERNANCE_MIN_WEIGHT"] = "3.0"
        status = GovernanceEngine().participation_status(
            {"vote_count": 2, "participation_weight": 2.0}, "tuning"
        )
        assert status["voters_met"] is True
        assert status["weight_met"] is False
        assert status["participation_met"] is False
        assert status["weight_progress"] == round(2.0 / 3.0, 4)
    finally:
        _clear_env()


# --------------------------------------------------------------------------- #
# Live cast_vote path: floor gates auto-enact AND auto-reject
# --------------------------------------------------------------------------- #

def test_single_yes_held_open_then_second_yes_enacts():
    _clear_env()  # default floor = 2 voters
    _del_setting("swarm.min_cs_threshold")
    dbid = _raise("tuning", "compliance correction required after cs degradation")
    eng = GovernanceEngine()

    assert eng.cast_vote(dbid, "a", "yes") is True
    assert _prop_status(dbid) == "open", "a single unanimous yes must be held open below the floor"

    assert eng.cast_vote(dbid, "b", "yes") is True
    assert _prop_status(dbid) == "enacted", "a second yes clears the floor and meets quorum"

    # The enactment audit snapshot records the participation that cleared the floor.
    changes = _enactment_changes(dbid)
    assert changes[-1]["participation"]["voters"] == 2
    assert changes[-1]["participation"]["min_voters"] == 2


def test_single_no_not_auto_rejected_below_floor():
    _clear_env()  # default floor = 2 voters
    dbid = _raise("tuning", "compliance correction")
    eng = GovernanceEngine()

    assert eng.cast_vote(dbid, "a", "no") is True
    assert _prop_status(dbid) == "open", "a single no must not auto-reject below the floor"

    # A second no clears the floor; no_ratio 1.0 > (1 - 0.5) => rejected.
    assert eng.cast_vote(dbid, "b", "no") is True
    assert _prop_status(dbid) == "rejected"


def test_floor_of_one_restores_single_vote_enact():
    _clear_env()
    _del_setting("swarm.min_cs_threshold")
    try:
        os.environ["GOVERNANCE_MIN_VOTERS"] = "1"
        os.environ["GOVERNANCE_MIN_WEIGHT"] = "0"
        dbid = _raise("tuning", "compliance correction required after cs degradation")
        eng = GovernanceEngine()
        assert eng.cast_vote(dbid, "solo", "yes") is True
        assert _prop_status(dbid) == "enacted", "a floor of 1 restores the legacy single-vote enact"
    finally:
        _clear_env()


def test_abstain_is_participation_but_not_a_decision():
    _clear_env()  # default floor = 2 voters
    dbid = _raise("tuning", "compliance correction")
    eng = GovernanceEngine()

    # Two abstains meet the voter floor but leave total_effective at 0 => no decision.
    assert eng.cast_vote(dbid, "a", "abstain") is True
    assert eng.cast_vote(dbid, "b", "abstain") is True
    assert _prop_status(dbid) == "open"

    db = SessionLocal()
    try:
        tally = eng._resolve_vote_tally(db, dbid)
    finally:
        db.close()
    assert tally["participation_weight"] == 2.0, "abstains count toward participation weight"
    assert tally["total_effective"] == 0.0, "abstains are not effective yes/no votes"
    status = eng.participation_status(tally, "tuning")
    assert status["participation_met"] is True


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #

_TESTS = [
    test_participation_floor_defaults,
    test_participation_floor_global_and_per_track_overrides,
    test_participation_floor_malformed_value_falls_back,
    test_participation_floor_clamps_negative,
    test_participation_status_voters_and_progress,
    test_participation_status_weight_dimension_gates_independently,
    test_single_yes_held_open_then_second_yes_enacts,
    test_single_no_not_auto_rejected_below_floor,
    test_floor_of_one_restores_single_vote_enact,
    test_abstain_is_participation_but_not_a_decision,
]


def main():
    _snapshot()
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
