"""Tests for the governance quorum -> enact -> AppSetting tuning loop.

Regression coverage for ``src/swarm/governance_engine.py`` +
``src/swarm/governance_settings.py``. Enacted proposals silently change live
swarm behavior (CS thresholds, provider quarantine, halt-on-critical), so a
regression here could let the governance loop stop applying tuning without
anyone noticing. These tests pin the full loop:

  * a tuning-track proposal auto-enacts at quorum and writes the mapped
    AppSetting key, and the orchestrator's GovernanceSettings.load() reads it;
  * a safety-track proposal does NOT auto-enact on quorum (even unanimous yes)
    and only applies via an explicit operator_enact();
  * delegation resolution transfers weight into the tally (direct resolver test
    + the live cast_vote enactment path);
  * GovernanceSettings.load() clamps out-of-bounds enacted values and parses
    bool/int/float keys.

The engine is DB-backed, so this suite runs against the real dev database and
cleans up its own rows + restores any pre-existing swarm.* AppSetting values.

Runnable two ways:
  * python tests/test_governance_tuning.py   (standalone; exits non-zero on failure)
  * pytest tests/test_governance_tuning.py    (test_* functions; no plugins needed)
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
from src.swarm.governance_engine import GovernanceEngine
from src.swarm.governance_settings import GovernanceSettings

# Keys the suite reads/writes; snapshotted before any test runs and restored at
# teardown so we never leave (or clobber) live tuning settings.
_TOUCHED_SETTING_KEYS = [
    "swarm.min_cs_threshold",
    "swarm.halt_on_critical",
    "swarm.min_evidence_strength",
    "swarm.extra_debate_rounds",
    "swarm.max_concurrency",
    "swarm.quarantine_flagged_providers",
    "swarm.enable_skeptic_override",
]

_CREATED_PROPOSAL_DB_IDS = []
_SETTINGS_SNAPSHOT = {}
_SNAPSHOT_TAKEN = False


# --------------------------------------------------------------------------- #
# DB helpers
# --------------------------------------------------------------------------- #

def _raise(track, proposed_action, *, title="gov-test", origin="operator"):
    """Raise a proposal and return its integer db id (tracked for cleanup)."""
    pid_str = GovernanceEngine().raise_proposal(
        origin=origin,
        track=track,
        title=title,
        description="governance tuning regression test",
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
    """Return parsed change_json dicts for every enactment of a proposal."""
    db = SessionLocal()
    try:
        rows = db.query(GovernanceEnactment).filter_by(proposal_id=dbid).all()
        return [json.loads(r.change_json or "{}") for r in rows]
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Snapshot / cleanup
# --------------------------------------------------------------------------- #

def _snapshot_settings():
    global _SNAPSHOT_TAKEN
    if _SNAPSHOT_TAKEN:
        return
    for key in _TOUCHED_SETTING_KEYS:
        _SETTINGS_SNAPSHOT[key] = _setting(key)
    _SNAPSHOT_TAKEN = True


def _cleanup():
    # Delete proposal artifacts (votes, enactments, proposals).
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

    # Restore touched settings to their pre-test state.
    if _SNAPSHOT_TAKEN:
        for key, original in _SETTINGS_SNAPSHOT.items():
            if original is None:
                _del_setting(key)
            else:
                _set_setting(key, original)


_snapshot_settings()
atexit.register(_cleanup)


# --------------------------------------------------------------------------- #
# Tuning track: auto-enacts at quorum and writes the mapped AppSetting
# --------------------------------------------------------------------------- #

def test_tuning_proposal_auto_enacts_at_quorum_and_writes_appsetting():
    _del_setting("swarm.min_cs_threshold")
    dbid = _raise("tuning", "compliance correction required after cs degradation")

    eng = GovernanceEngine()
    # A single yes vote => yes_ratio 1.0 >= tuning quorum (0.50) => auto-enact.
    assert eng.cast_vote(dbid, "operator", "yes") is True

    assert _prop_status(dbid) == "enacted", "tuning proposal must auto-enact at quorum"
    assert _setting("swarm.min_cs_threshold") == "50", "mapped AppSetting not written"

    changes = _enactment_changes(dbid)
    assert len(changes) == 1, "exactly one enactment row expected"
    assert changes[0]["method"] == "quorum"
    assert "swarm.min_cs_threshold=50" in changes[0]["applied_settings"]

    # Loop closes: the orchestrator's settings loader reads the enacted key.
    gs = GovernanceSettings.load()
    assert gs.min_cs_threshold == 50


# --------------------------------------------------------------------------- #
# Safety track: NO auto-enact on quorum; applies only via operator_enact
# --------------------------------------------------------------------------- #

def test_safety_proposal_no_auto_enact_then_operator_enact_applies():
    _del_setting("swarm.halt_on_critical")
    dbid = _raise("safety", "halt pipeline immediately on the next critical breach")

    eng = GovernanceEngine()
    # Even a unanimous yes must NOT auto-enact a safety-track proposal.
    assert eng.cast_vote(dbid, "v1", "yes") is True
    assert eng.cast_vote(dbid, "v2", "yes") is True

    assert _prop_status(dbid) == "open", "safety proposal must not auto-enact on quorum"
    assert _setting("swarm.halt_on_critical") != "true", "safety setting applied without operator action"

    # Explicit operator action is the only path that enacts + applies it.
    assert eng.operator_enact(dbid, operator="tester") is True
    assert _prop_status(dbid) == "enacted"
    assert _setting("swarm.halt_on_critical") == "true"

    changes = _enactment_changes(dbid)
    assert changes[-1]["method"] == "operator_direct"
    assert "swarm.halt_on_critical=true" in changes[-1]["applied_settings"]

    gs = GovernanceSettings.load()
    assert gs.halt_on_critical is True


# --------------------------------------------------------------------------- #
# Delegation resolution affecting the tally
# --------------------------------------------------------------------------- #

def test_delegation_resolution_transfers_weight_in_tally():
    """Direct test of the resolver: delegated weight follows the chain to the
    terminal voter's direction (including a multi-hop chain)."""
    dbid = _raise("tuning", "compliance correction")  # action irrelevant here
    eng = GovernanceEngine()

    db = SessionLocal()
    try:
        # Insert votes directly so _maybe_enact never fires and we isolate the
        # tally resolver. a=yes, c=no; b->a, d->c, e->b->a (multi-hop).
        db.add_all([
            GovernanceVote(proposal_id=dbid, voter="a", vote="yes", weight=1.0),
            GovernanceVote(proposal_id=dbid, voter="b", vote="delegated", weight=1.0,
                           reason="delegated_to:a"),
            GovernanceVote(proposal_id=dbid, voter="c", vote="no", weight=1.0),
            GovernanceVote(proposal_id=dbid, voter="d", vote="delegated", weight=1.0,
                           reason="delegated_to:c"),
            GovernanceVote(proposal_id=dbid, voter="e", vote="delegated", weight=1.0,
                           reason="delegated_to:b"),
        ])
        db.commit()
        tally = eng._resolve_vote_tally(db, dbid)
    finally:
        db.close()

    assert tally["yes"] == 3.0, "a + b(->a) + e(->b->a) must total 3 yes weight"
    assert tally["no"] == 2.0, "c + d(->c) must total 2 no weight"
    assert tally["total_effective"] == 5.0
    assert tally["yes_ratio"] == round(3.0 / 5.0, 4)
    assert tally["delegation_map"] == {"b": "a", "d": "c", "e": "b"}


def test_delegation_feeds_live_enactment_tally():
    """The live cast_vote path counts delegated weight: a delegation cast before
    the delegate votes does not decide, then the delegate's yes enacts with the
    transferred weight recorded in the enactment tally snapshot."""
    _del_setting("swarm.min_cs_threshold")
    dbid = _raise("tuning", "compliance correction")
    eng = GovernanceEngine()

    # Delegate first; delegate has not voted yet => resolves to abstain =>
    # no effective votes => no decision (proposal stays open).
    assert eng.delegate(dbid, delegator="bob", delegate_to="alice") is True
    assert _prop_status(dbid) == "open", "delegation alone must not decide a proposal"

    # Alice votes yes => bob's delegated weight transfers => quorum met => enact.
    assert eng.cast_vote(dbid, "alice", "yes") is True
    assert _prop_status(dbid) == "enacted"

    changes = _enactment_changes(dbid)
    assert changes[-1]["delegation_map"] == {"bob": "alice"}
    assert changes[-1]["total_effective"] == 2.0, "delegated weight must count in the tally"


# --------------------------------------------------------------------------- #
# GovernanceSettings.load() bounds clamping + key parsing
# --------------------------------------------------------------------------- #

def test_governance_settings_clamps_out_of_bounds_values():
    # Upper bounds.
    _set_setting("swarm.min_cs_threshold", "999")
    _set_setting("swarm.min_evidence_strength", "5.0")
    _set_setting("swarm.extra_debate_rounds", "99")
    _set_setting("swarm.max_concurrency", "999")
    gs = GovernanceSettings.load()
    assert gs.min_cs_threshold == 100
    assert gs.min_evidence_strength == 1.0
    assert gs.extra_debate_rounds == 5
    assert gs.max_concurrency == 32

    # Lower bounds.
    _set_setting("swarm.min_cs_threshold", "-5")
    _set_setting("swarm.min_evidence_strength", "-1")
    _set_setting("swarm.max_concurrency", "-10")
    gs = GovernanceSettings.load()
    assert gs.min_cs_threshold == 0
    assert gs.min_evidence_strength == 0.0
    assert gs.max_concurrency == 0


def test_governance_settings_parses_enacted_bool_keys():
    _set_setting("swarm.halt_on_critical", "true")
    _set_setting("swarm.quarantine_flagged_providers", "yes")
    _set_setting("swarm.enable_skeptic_override", "0")
    gs = GovernanceSettings.load()
    assert gs.halt_on_critical is True
    assert gs.quarantine_flagged_providers is True
    assert gs.enable_skeptic_override is False


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #

_TESTS = [
    test_tuning_proposal_auto_enacts_at_quorum_and_writes_appsetting,
    test_safety_proposal_no_auto_enact_then_operator_enact_applies,
    test_delegation_resolution_transfers_weight_in_tally,
    test_delegation_feeds_live_enactment_tally,
    test_governance_settings_clamps_out_of_bounds_values,
    test_governance_settings_parses_enacted_bool_keys,
]


def main():
    _snapshot_settings()
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
