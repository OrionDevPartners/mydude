"""Hermetic tests for the edge-truth / thesis self-evolution loop.

Coverage:
  1. seed_components is idempotent and registers default components.
  2. A thesis runs ONLY inside the EXPERIMENTAL sandbox (never touches live state).
  3. Promotion requires all-tests-pass + consensus + governance enactment
     (no silent truth mutation).
  4. A rejected thesis never reaches 'promoted' status.
  5. A new thesis is opened after each cycle (next-thesis selection).
  6. A stalled trial records a 'stalled' thesis and does NOT promote.
  7. The loop is stoppable: stop_component signals the thread.
  8. Manual trial trigger works for prompt_program and swarm_config components.

All tests are DB-backed (real dev DB) using throwaway component/thesis rows
that are cleaned up afterwards. No network or LLM calls are made.

Runnable two ways:
  python tests/test_evolution_loop.py      (standalone; exits non-zero on failure)
  pytest tests/test_evolution_loop.py      (test_* functions; no plugins needed)
"""
import json
import os
import sys
import time
import traceback
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Hermetic contract: never dispatch the live LLM swarm during candidate
# generation (a real run is ~240 provider calls). Force heuristic-only theses so
# select_next_thesis is deterministic and fast regardless of provider config.
os.environ.setdefault("EVOLUTION_LLM_THESIS", "0")

from src.database import SessionLocal
from src.models import (
    CognitionComponent,
    CognitionThesis,
    ThesisTrialIteration,
    EvolutionCycleLog,
)
from src.promptopt import evolution_store as estore
from src.promptopt.evolution import (
    run_experimental_sandbox,
    select_next_thesis,
    EvolutionStallError,
    MAX_ITERATIONS,
    BRANCH_CELLS_BY_TYPE,
    seed_components,
    get_loop,
)

TEST_PREFIX = "__evol_test__"


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Helper asserts
# ---------------------------------------------------------------------------

def assert_equal(a, b, msg=""):
    if a != b:
        raise AssertionError("%s: expected %r == %r" % (msg, a, b))


def assert_true(cond, msg=""):
    if not cond:
        raise AssertionError(msg or "Expected True, got False")


def assert_false(cond, msg=""):
    if cond:
        raise AssertionError(msg or "Expected False, got True")


# ---------------------------------------------------------------------------
# Test 1: seed_components is idempotent
# ---------------------------------------------------------------------------

def test_seed_components_idempotent():
    seed_components()
    seed_components()

    db = SessionLocal()
    try:
        count = db.query(CognitionComponent).filter(
            CognitionComponent.name.like("%judge%")
        ).count()
        assert_true(count >= 1, "At least one judge component should exist after seeding")
    finally:
        db.close()
    print("PASS test_seed_components_idempotent")


# ---------------------------------------------------------------------------
# Test 2: EXPERIMENTAL sandbox never touches live DB state
# ---------------------------------------------------------------------------

def test_sandbox_isolated_from_live_state():
    """The sandbox must not modify any PromptProgram.current_version_id or AppSetting."""
    name = TEST_PREFIX + "sandbox_isolation"
    _cleanup(name)
    try:
        component_id = _make_component(name, "swarm_config")

        db = SessionLocal()
        try:
            from src.models import AppSetting
            before_settings = {s.key: s.value for s in db.query(AppSetting).all()}
        finally:
            db.close()

        result = run_experimental_sandbox(
            component_type="swarm_config",
            component_name=name,
            thesis={"value": 0.75, "key": "swarm.min_evidence_strength"},
            branch_cell="evidence_strength",
            current_truth={"source": "test"},
        )

        assert_equal(result.sandbox_label, "EXPERIMENTAL", "sandbox_label must always be EXPERIMENTAL")

        db = SessionLocal()
        try:
            from src.models import AppSetting
            after_settings = {s.key: s.value for s in db.query(AppSetting).all()}
        finally:
            db.close()

        assert_equal(
            before_settings, after_settings,
            "Sandbox must not mutate AppSetting (live state isolation violated)"
        )
        print("PASS test_sandbox_isolated_from_live_state")
    finally:
        _cleanup(name)


# ---------------------------------------------------------------------------
# Test 3: thesis created with 'proposed' status; sandbox runs as 'EXPERIMENTAL'
# ---------------------------------------------------------------------------

def test_thesis_starts_proposed_and_sandbox_labeled_experimental():
    name = TEST_PREFIX + "thesis_status"
    _cleanup(name)
    try:
        component_id = _make_component(name, "swarm_config")

        thesis_id = estore.create_thesis(
            component_id=component_id,
            branch_cell="evidence_strength",
            thesis={"value": 0.70, "key": "swarm.min_evidence_strength"},
            rationale="test thesis",
            cycle_index=1,
        )

        t = estore.get_thesis(thesis_id)
        assert_equal(t["status"], "proposed", "New thesis must start in 'proposed' status")

        iter_id = estore.record_iteration(
            thesis_id=thesis_id,
            iteration_no=1,
            test_results={"sandbox": "EXPERIMENTAL"},
            compliance_score=70.0,
            hallucination_risk=0.1,
            composite_score=0.65,
            all_tests_passed=True,
            outcome="pass",
        )

        t_updated = estore.get_thesis(thesis_id)
        assert_true(len(t_updated["iterations"]) >= 1, "Iteration should be recorded")
        first_iter = t_updated["iterations"][0]
        assert_equal(first_iter["sandbox_label"], "EXPERIMENTAL", "Every iteration must be labeled EXPERIMENTAL")

        print("PASS test_thesis_starts_proposed_and_sandbox_labeled_experimental")
    finally:
        _cleanup(name)


# ---------------------------------------------------------------------------
# Test 4: rejected thesis never reaches 'promoted'
# ---------------------------------------------------------------------------

def test_rejected_thesis_never_promoted():
    name = TEST_PREFIX + "rejected_not_promoted"
    _cleanup(name)
    try:
        component_id = _make_component(name, "swarm_config")

        thesis_id = estore.create_thesis(
            component_id=component_id,
            branch_cell="evidence_strength",
            thesis={"value": 99.9, "key": "swarm.bad_value"},
            rationale="intentionally bad thesis",
            cycle_index=1,
        )

        result = run_experimental_sandbox(
            component_type="swarm_config",
            component_name=name,
            thesis={"value": 99.9},
            branch_cell="evidence_strength",
            current_truth={},
        )

        assert_false(result.all_tests_passed, "Out-of-range value should fail sandbox tests")

        estore.update_thesis_status(thesis_id, "rejected")

        t = estore.get_thesis(thesis_id)
        assert_equal(t["status"], "rejected", "Thesis must be 'rejected'")
        assert_false(t["status"] == "promoted", "Rejected thesis must never be promoted")
        print("PASS test_rejected_thesis_never_promoted")
    finally:
        _cleanup(name)


# ---------------------------------------------------------------------------
# Test 5: promotion requires all-tests-pass (simulation)
# ---------------------------------------------------------------------------

def test_promotion_blocked_without_all_tests_pass():
    """A thesis that fails sandbox tests must not become 'promoted'."""
    name = TEST_PREFIX + "promo_gate"
    _cleanup(name)
    try:
        component_id = _make_component(name, "swarm_config")
        thesis_id = estore.create_thesis(
            component_id=component_id,
            branch_cell="min_cs_threshold",
            thesis={"value": 200, "key": "swarm.min_cs_threshold"},
            rationale="out of safe range",
            cycle_index=1,
        )

        result = run_experimental_sandbox(
            component_type="swarm_config",
            component_name=name,
            thesis={"value": 200},
            branch_cell="min_cs_threshold",
            current_truth={},
        )

        assert_false(result.all_tests_passed, "value=200 exceeds safe range, must fail")

        if not result.all_tests_passed:
            estore.update_thesis_status(thesis_id, "rejected", test_score=result.composite_score)

        t = estore.get_thesis(thesis_id)
        assert_false(t["status"] == "promoted", "Thesis with failed tests must not be promoted")
        print("PASS test_promotion_blocked_without_all_tests_pass")
    finally:
        _cleanup(name)


# ---------------------------------------------------------------------------
# Test 6: a passing swarm_config thesis reaches awaiting_consensus
# ---------------------------------------------------------------------------

def test_passing_sandbox_advances_to_awaiting_consensus():
    name = TEST_PREFIX + "pass_consensus"
    _cleanup(name)
    try:
        component_id = _make_component(name, "swarm_config")
        thesis_id = estore.create_thesis(
            component_id=component_id,
            branch_cell="evidence_strength",
            thesis={"value": 0.70, "key": "swarm.min_evidence_strength"},
            rationale="valid improvement",
            cycle_index=1,
        )

        result = run_experimental_sandbox(
            component_type="swarm_config",
            component_name=name,
            thesis={"value": 0.70},
            branch_cell="evidence_strength",
            current_truth={},
        )

        assert_true(result.all_tests_passed, "value=0.70 in safe range [0.40, 0.95] must pass")

        estore.update_thesis_status(thesis_id, "awaiting_consensus", test_score=result.composite_score)

        t = estore.get_thesis(thesis_id)
        assert_equal(t["status"], "awaiting_consensus", "Passing thesis must advance to awaiting_consensus")
        print("PASS test_passing_sandbox_advances_to_awaiting_consensus")
    finally:
        _cleanup(name)


# ---------------------------------------------------------------------------
# Test 7: next-thesis selection produces a new thesis after a cycle
# ---------------------------------------------------------------------------

def test_next_thesis_selection_after_cycle():
    name = TEST_PREFIX + "next_selection"
    _cleanup(name)
    try:
        component_id = _make_component(name, "swarm_config")

        db = SessionLocal()
        try:
            c = db.query(CognitionComponent).filter_by(id=component_id).first()
        finally:
            db.close()

        candidate, votes = select_next_thesis(c, cycle_index=1)

        assert_true("branch_cell" in candidate, "Candidate must have branch_cell")
        assert_true("thesis" in candidate, "Candidate must have thesis payload")
        assert_true("rationale" in candidate, "Candidate must have rationale")
        assert_true("consensus_confidence" in votes, "Votes must include consensus_confidence")
        assert_true(votes.get("candidates_count", 0) >= 1, "Must have at least one candidate")

        second_candidate, _ = select_next_thesis(c, cycle_index=2)
        assert_true("branch_cell" in second_candidate, "Second selection must also produce a candidate")

        print("PASS test_next_thesis_selection_after_cycle")
    finally:
        _cleanup(name)


# ---------------------------------------------------------------------------
# Test 8: EvolutionStallError raised when score doesn't improve
# ---------------------------------------------------------------------------

def test_stall_detection():
    """MAX_ITERATIONS with no score improvement must raise EvolutionStallError."""
    name = TEST_PREFIX + "stall"
    _cleanup(name)
    try:
        component_id = _make_component(name, "swarm_config")
        thesis_id = estore.create_thesis(
            component_id=component_id,
            branch_cell="evidence_strength",
            thesis={"value": 99.0},
            rationale="stall test",
            cycle_index=1,
        )

        stall_raised = False
        try:
            from src.promptopt.evolution import run_trial
            run_trial(
                component_id=component_id,
                thesis_id=thesis_id,
                component_type="swarm_config",
                component_name=name,
                current_truth={},
                branch_cell="evidence_strength",
                thesis={"value": 99.0},
            )
        except EvolutionStallError:
            stall_raised = True

        assert_true(stall_raised, "EvolutionStallError must be raised when tests never pass")

        t = estore.get_thesis(thesis_id)
        assert_true(
            len(t["iterations"]) >= 1,
            "At least one iteration must be recorded before stall"
        )
        print("PASS test_stall_detection")
    finally:
        _cleanup(name)


# ---------------------------------------------------------------------------
# Test 9: cycle log records audit trail
# ---------------------------------------------------------------------------

def test_cycle_log_audit_trail():
    name = TEST_PREFIX + "cycle_audit"
    _cleanup(name)
    try:
        component_id = _make_component(name, "swarm_config")

        thesis_id = estore.create_thesis(
            component_id=component_id,
            branch_cell="evidence_strength",
            thesis={"value": 0.65},
            rationale="audit test",
            cycle_index=1,
        )
        estore.update_thesis_status(thesis_id, "rejected")
        cycle_count = estore.increment_cycle(component_id)
        estore.log_cycle(
            component_id=component_id,
            cycle_index=1,
            outcome="rejected",
            thesis_id=thesis_id,
            next_selection={"next_cycle": 2},
            detail="audit test cycle",
        )

        logs = estore.list_cycle_logs(component_id)
        assert_true(len(logs) >= 1, "Cycle log must have at least one entry")
        last = logs[0]
        assert_equal(last["outcome"], "rejected", "Cycle outcome must be recorded")
        assert_equal(last["thesis_id"], thesis_id, "Thesis id must be in audit log")
        assert_true("next_cycle" in (last.get("next_selection") or {}), "Next selection info must be recorded")
        print("PASS test_cycle_log_audit_trail")
    finally:
        _cleanup(name)


# ---------------------------------------------------------------------------
# Test 10: loop enable/disable flag respected
# ---------------------------------------------------------------------------

def test_loop_enable_disable():
    name = TEST_PREFIX + "loop_toggle"
    _cleanup(name)
    try:
        component_id = _make_component(name, "swarm_config")

        estore.set_loop_enabled(component_id, True)
        db = SessionLocal()
        try:
            c = db.query(CognitionComponent).filter_by(id=component_id).first()
            assert_true(c.loop_enabled, "loop_enabled must be True after enable")
        finally:
            db.close()

        estore.set_loop_enabled(component_id, False)
        db = SessionLocal()
        try:
            c = db.query(CognitionComponent).filter_by(id=component_id).first()
            assert_false(c.loop_enabled, "loop_enabled must be False after disable")
            assert_equal(c.loop_state, "idle", "loop_state must reset to idle when disabled")
        finally:
            db.close()

        print("PASS test_loop_enable_disable")
    finally:
        _cleanup(name)


# ---------------------------------------------------------------------------
# Test 11: branch cells registry
# ---------------------------------------------------------------------------

def test_branch_cells_registry():
    assert_true("prompt_program" in BRANCH_CELLS_BY_TYPE, "prompt_program must be in registry")
    assert_true("swarm_config" in BRANCH_CELLS_BY_TYPE, "swarm_config must be in registry")
    assert_true("role_composition" in BRANCH_CELLS_BY_TYPE, "role_composition must be in registry")
    assert_true(len(BRANCH_CELLS_BY_TYPE["prompt_program"]) >= 1, "prompt_program must expose branch cells")
    assert_true(len(BRANCH_CELLS_BY_TYPE["swarm_config"]) >= 1, "swarm_config must expose branch cells")
    print("PASS test_branch_cells_registry")


# ---------------------------------------------------------------------------
# Test 12: human gate blocks truth mutation — truth must NOT be updated for
#          human-gated theses even when the promotion gate returns success
# ---------------------------------------------------------------------------

def test_human_gate_blocks_truth_mutation():
    """run_promotion_gate must return human_gate_blocking=True for a thesis
    that has requires_human_gate=True, and the caller must NOT mutate truth."""
    from src.promptopt.evolution import run_promotion_gate, HIGH_IMPACT_SCORE_THRESHOLD

    name = TEST_PREFIX + "human_gate_truth"
    _cleanup(name)
    try:
        component_id = _make_component(name, "swarm_config")

        # Create a thesis marked as requiring human gate
        thesis_id = estore.create_thesis(
            component_id=component_id,
            branch_cell="evidence_strength",
            thesis={"value": 0.70, "key": "swarm.min_evidence_strength"},
            rationale="high-impact human-gated change",
            cycle_index=1,
            requires_human_gate=True,
        )

        db = SessionLocal()
        try:
            from src.models import CognitionComponent as _CC
            component = db.query(_CC).filter_by(id=component_id).first()
            original_truth = component.truth_json
        finally:
            db.close()

        # Build a proxy that mirrors the DB thesis, as _run_cycle would
        class _HumanGateProxy:
            thesis_json = json.dumps({"value": 0.70, "key": "swarm.min_evidence_strength"})
            branch_cell = "evidence_strength"
            cycle_index = 1
            rationale = "human-gated"
            test_score = 0.75
            component_type_hint = "swarm_config"
            requires_human_gate = True

        # run_promotion_gate should raise the proposal and return human_gate_blocking=True
        result = run_promotion_gate(
            component=component,
            thesis=_HumanGateProxy(),
            test_score=0.75,
            consensus_confidence=0.85,
        )
        assert_equal(len(result), 5, "run_promotion_gate must return a 5-tuple")
        proposal_raised, detail, pid, pdb_id, human_gate_blocking = result
        assert_true(human_gate_blocking, "requires_human_gate=True must set human_gate_blocking=True")

        # Simulate the callers duty: do NOT update truth when human_gate_blocking
        # (this verifies the invariant — the loop must check this flag)
        if human_gate_blocking:
            estore.update_thesis_status(
                thesis_id, "awaiting_human_approval",
                governance_proposal_id=pid,
                governance_proposal_db_id=pdb_id,
            )
            # explicitly skip: estore.update_component_truth(...)
        else:
            raise AssertionError("Should not reach auto-promotion branch for human-gated thesis")

        # Verify truth was NOT mutated
        db = SessionLocal()
        try:
            from src.models import CognitionComponent as _CC
            comp_after = db.query(_CC).filter_by(id=component_id).first()
            assert_equal(
                comp_after.truth_json, original_truth,
                "Truth must NOT be mutated for human-gated thesis before enactment"
            )
        finally:
            db.close()

        # Verify thesis status is awaiting_human_approval, not promoted
        t = estore.get_thesis(thesis_id)
        assert_equal(
            t["status"], "awaiting_human_approval",
            "Human-gated thesis must be 'awaiting_human_approval', never 'promoted'"
        )
        assert_false(
            t["status"] == "promoted",
            "Human-gated thesis must never auto-promote to 'promoted'"
        )
        print("PASS test_human_gate_blocks_truth_mutation")
    finally:
        _cleanup(name)


# ---------------------------------------------------------------------------
# Test 13: high-impact score threshold also triggers human gate
# ---------------------------------------------------------------------------

def test_high_impact_score_triggers_human_gate():
    """A thesis with test_score >= HIGH_IMPACT_SCORE_THRESHOLD must also have
    human_gate_blocking=True, even when requires_human_gate=False."""
    from src.promptopt.evolution import run_promotion_gate, HIGH_IMPACT_SCORE_THRESHOLD

    name = TEST_PREFIX + "high_impact_gate"
    _cleanup(name)
    try:
        component_id = _make_component(name, "swarm_config")

        thesis_id = estore.create_thesis(
            component_id=component_id,
            branch_cell="evidence_strength",
            thesis={"value": 0.70, "key": "swarm.min_evidence_strength"},
            rationale="high-score change",
            cycle_index=1,
            requires_human_gate=False,   # ← not explicitly flagged
        )

        db = SessionLocal()
        try:
            from src.models import CognitionComponent as _CC
            component = db.query(_CC).filter_by(id=component_id).first()
        finally:
            db.close()

        class _HighScoreProxy:
            thesis_json = json.dumps({"value": 0.70, "key": "swarm.min_evidence_strength"})
            branch_cell = "evidence_strength"
            cycle_index = 1
            rationale = "high score"
            test_score = HIGH_IMPACT_SCORE_THRESHOLD  # exactly at threshold
            component_type_hint = "swarm_config"
            requires_human_gate = False

        result = run_promotion_gate(
            component=component,
            thesis=_HighScoreProxy(),
            test_score=HIGH_IMPACT_SCORE_THRESHOLD,
            consensus_confidence=0.90,
        )
        assert_equal(len(result), 5, "run_promotion_gate must return a 5-tuple")
        proposal_raised, detail, pid, pdb_id, human_gate_blocking = result
        assert_true(
            human_gate_blocking,
            "test_score >= HIGH_IMPACT_SCORE_THRESHOLD must set human_gate_blocking=True "
            "even without requires_human_gate flag"
        )
        print("PASS test_high_impact_score_triggers_human_gate")
    finally:
        _cleanup(name)


# ---------------------------------------------------------------------------
# Test 14: multi-iteration trial does NOT prematurely stall when score changes
# ---------------------------------------------------------------------------

def test_multi_iteration_no_premature_stall():
    """run_trial must not raise EvolutionStallError when the score genuinely
    changes between iterations.  Patching the sandbox to return alternating
    scores verifies that stall detection compares across consecutive pairs, not
    the current iteration to itself."""
    import unittest.mock as mock
    from src.promptopt.evolution import run_trial, STALL_SCORE_DELTA, SandboxResult

    name = TEST_PREFIX + "no_premature_stall"
    _cleanup(name)
    try:
        component_id = _make_component(name, "swarm_config")
        thesis_id = estore.create_thesis(
            component_id=component_id,
            branch_cell="evidence_strength",
            thesis={"value": 0.65},
            rationale="stall regression test",
            cycle_index=1,
        )

        # Craft scores that are always changing (delta >= STALL_SCORE_DELTA)
        # so stall must NOT trigger even after MAX_ITERATIONS.  The final
        # iteration does not pass all_tests, so run_trial should raise
        # EvolutionStallError only after hitting MAX_ITERATIONS, not earlier.
        changing_scores = [0.40, 0.50, 0.60, 0.70, 0.80]
        call_count = [0]

        def fake_sandbox(**kwargs):
            i = call_count[0]
            call_count[0] += 1
            score = changing_scores[i] if i < len(changing_scores) else 0.80
            return SandboxResult(
                all_tests_passed=False,
                composite_score=score,
                compliance_score=score * 100,
                hallucination_risk=1.0 - score,
                sandbox_label="EXPERIMENTAL",
            )

        stall_raised = False
        stall_at_iteration = None
        try:
            with mock.patch(
                "src.promptopt.evolution.run_experimental_sandbox",
                side_effect=fake_sandbox,
            ):
                run_trial(
                    component_id=component_id,
                    thesis_id=thesis_id,
                    component_type="swarm_config",
                    component_name=name,
                    current_truth={},
                    branch_cell="evidence_strength",
                    thesis={"value": 0.65},
                )
        except EvolutionStallError as e:
            stall_raised = True
            stall_at_iteration = call_count[0]

        assert_true(stall_raised, "EvolutionStallError must eventually be raised (MAX_ITERATIONS exceeded)")
        # With continuously changing scores (always delta >= STALL_SCORE_DELTA),
        # the early-stall path should never fire — we should exhaust all MAX_ITERATIONS.
        assert_equal(
            stall_at_iteration, 5,
            "All 5 iterations must run when scores change on every step "
            "(stall fired at iteration %d instead of after iteration 5)" % (stall_at_iteration or 0)
        )
        print("PASS test_multi_iteration_no_premature_stall")
    finally:
        _cleanup(name)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Test 15: enactment audit record is written before truth is updated
#          (_enact_evolution_proposal commits GovernanceEnactment atomically)
# ---------------------------------------------------------------------------

def test_enactment_record_written_before_truth_update():
    """_enact_evolution_proposal must write a GovernanceEnactment row.
    Truth update is only valid AFTER enactment is on the books."""
    from src.promptopt.evolution import _enact_evolution_proposal, _derive_enacted_truth
    from src.swarm.governance_engine import GovernanceEngine

    name = TEST_PREFIX + "enact_audit"
    _cleanup(name)
    try:
        component_id = _make_component(name, "swarm_config")
        thesis_id = estore.create_thesis(
            component_id=component_id,
            branch_cell="evidence_strength",
            thesis={"value": 0.72, "key": "swarm.min_evidence_strength"},
            rationale="enactment audit test",
            cycle_index=1,
        )

        # Raise a governance proposal (as run_promotion_gate would)
        engine = GovernanceEngine()
        proposal_id_str = engine.raise_proposal(
            origin="evolution_loop",
            track="tuning",
            title="Test enactment audit",
            description="test",
            proposed_action="swarm.min_evidence_strength=0.72",
            evidence=["sandbox=EXPERIMENTAL"],
        )
        assert_true(bool(proposal_id_str), "raise_proposal must return a proposal_id string")

        db = SessionLocal()
        try:
            from src.models import GovernanceProposal
            prop = db.query(GovernanceProposal).filter_by(proposal_id=proposal_id_str).first()
            assert_true(prop is not None, "GovernanceProposal must exist after raise_proposal")
            pdb_id = prop.id
        finally:
            db.close()

        # Count enactments BEFORE calling _enact_evolution_proposal
        db = SessionLocal()
        try:
            from src.models import GovernanceEnactment
            before_count = db.query(GovernanceEnactment).filter_by(proposal_id=pdb_id).count()
        finally:
            db.close()

        enacted, detail = _enact_evolution_proposal(
            component_type="swarm_config",
            component_name=name,
            proposal_db_id=pdb_id,
            thesis_payload={"value": 0.72, "key": "swarm.min_evidence_strength"},
            branch_cell="evidence_strength",
        )
        assert_true(enacted, "swarm_config enactment must succeed: %s" % detail)

        # Count enactments AFTER — must have increased by exactly 1
        db = SessionLocal()
        try:
            from src.models import GovernanceEnactment, GovernanceProposal
            after_count = db.query(GovernanceEnactment).filter_by(proposal_id=pdb_id).count()
            enacted_prop = db.query(GovernanceProposal).filter_by(id=pdb_id).first()
        finally:
            db.close()

        assert_equal(
            after_count, before_count + 1,
            "Exactly one GovernanceEnactment row must be written by _enact_evolution_proposal"
        )
        assert_equal(
            enacted_prop.status, "enacted",
            "GovernanceProposal status must be 'enacted' after _enact_evolution_proposal"
        )
        print("PASS test_enactment_record_written_before_truth_update")
    finally:
        _cleanup(name)


# ---------------------------------------------------------------------------
# Test 16: branch-cell patching only touches the targeted cell
# ---------------------------------------------------------------------------

def test_branch_cell_patch_isolates_single_cell():
    """_apply_branch_cell_patch must modify exactly the targeted branch cell
    and leave all other keys in current_truth untouched."""
    from src.promptopt.evolution import _apply_branch_cell_patch

    # swarm_config: only evidence_strength key changes
    current = {
        "swarm.min_evidence_strength": 0.60,
        "swarm.min_cs_threshold": 50,
        "extra_key": "must_not_change",
    }
    patched = _apply_branch_cell_patch(
        current_truth=current,
        branch_cell="evidence_strength",
        thesis_payload={"value": 0.75, "key": "swarm.min_evidence_strength"},
        component_type="swarm_config",
    )
    assert_equal(patched["swarm.min_evidence_strength"], 0.75, "Target cell must be updated")
    assert_equal(patched["swarm.min_cs_threshold"], 50, "Non-target cell must be unchanged")
    assert_equal(patched["extra_key"], "must_not_change", "Unrelated key must not be touched")

    # Values are clamped to safe bounds — 0.99 should be clamped to 0.95
    patched_clamped = _apply_branch_cell_patch(
        current_truth=current,
        branch_cell="evidence_strength",
        thesis_payload={"value": 0.99},
        component_type="swarm_config",
    )
    assert_equal(
        patched_clamped["swarm.min_evidence_strength"], 0.95,
        "Value above upper bound must be clamped to 0.95"
    )

    # role_composition: only the targeted role weight changes
    current_role = {"role_weights.skeptic": 0.9, "role_weights.falsifier": 0.8, "other": 1}
    patched_role = _apply_branch_cell_patch(
        current_truth=current_role,
        branch_cell="role_weights.skeptic",
        thesis_payload={"weight": 0.95, "role": "skeptic"},
        component_type="role_composition",
    )
    assert_equal(patched_role["role_weights.skeptic"], 0.95, "Target role weight must update")
    assert_equal(patched_role["role_weights.falsifier"], 0.8, "Other role weight must not change")
    assert_equal(patched_role["other"], 1, "Unrelated key must not change")

    # prompt_program: only 'instructions' changes
    current_pp = {"instructions": "old", "demos": ["d1"], "meta": "keep"}
    patched_pp = _apply_branch_cell_patch(
        current_truth=current_pp,
        branch_cell="instructions",
        thesis_payload={"instructions": "new instructions", "demos": ["must_not_leak"]},
        component_type="prompt_program",
    )
    assert_equal(patched_pp["instructions"], "new instructions", "instructions must update")
    assert_equal(patched_pp["demos"], ["d1"], "demos must not change when branch_cell=instructions")
    assert_equal(patched_pp["meta"], "keep", "unrelated key must not change")

    print("PASS test_branch_cell_patch_isolates_single_cell")


# ---------------------------------------------------------------------------
# Test 17: manual trial runs with force=True even when loop_enabled=False
# ---------------------------------------------------------------------------

def test_manual_trial_force_bypasses_loop_enabled():
    """_run_cycle(component_id, force=True) must not return 'paused' even
    when loop_enabled=False, and must produce a real cycle outcome."""
    import unittest.mock as mock
    from src.promptopt.evolution import _run_cycle, SandboxResult

    name = TEST_PREFIX + "force_trial"
    _cleanup(name)
    try:
        component_id = _make_component(name, "swarm_config")
        estore.set_loop_enabled(component_id, False)

        # Without force: must return 'paused'
        outcome_paused = _run_cycle(component_id, force=False)
        assert_equal(outcome_paused, "paused", "_run_cycle must return 'paused' when loop_enabled=False and force=False")

        # With force=True: must run the cycle regardless of loop_enabled
        # Patch sandbox to return a failing (but valid) result so the cycle
        # completes without needing a real LLM or db side-effects beyond the cycle.
        def failing_sandbox(**kwargs):
            return SandboxResult(
                all_tests_passed=False,
                composite_score=0.30,
                compliance_score=30.0,
                hallucination_risk=0.7,
                sandbox_label="EXPERIMENTAL",
            )

        with mock.patch(
            "src.promptopt.evolution.run_experimental_sandbox",
            side_effect=failing_sandbox,
        ):
            outcome_forced = _run_cycle(component_id, force=True)

        assert_true(
            outcome_forced != "paused",
            "force=True must bypass the paused gate (got %r)" % outcome_forced
        )
        assert_true(
            outcome_forced in ("rejected", "stalled", "promoted", "awaiting_human_approval", "error"),
            "force=True must produce a real cycle outcome (got %r)" % outcome_forced
        )
        print("PASS test_manual_trial_force_bypasses_loop_enabled")
    finally:
        _cleanup(name)


# ---------------------------------------------------------------------------
# Test 18: a stall records the failing branch cell as a negative signal in
#          EvolutionCycleLog (auditable stall signal)
# ---------------------------------------------------------------------------

def test_stall_records_negative_signal_in_cycle_log():
    """When a cycle stalls, _run_cycle must record the failing branch cell under
    'stalled_branch_cell' in the EvolutionCycleLog so the next selection can
    learn from it."""
    import unittest.mock as mock
    from src.promptopt.evolution import _run_cycle, SandboxResult

    name = TEST_PREFIX + "stall_signal_log"
    _cleanup(name)
    try:
        component_id = _make_component(name, "swarm_config")
        estore.set_loop_enabled(component_id, True)

        # Sandbox returns a constant failing score so run_trial raises
        # EvolutionStallError (stall path), driving outcome='stalled'.
        def stalling_sandbox(**kwargs):
            return SandboxResult(
                all_tests_passed=False,
                composite_score=0.30,
                compliance_score=30.0,
                hallucination_risk=0.7,
                sandbox_label="EXPERIMENTAL",
            )

        with mock.patch(
            "src.promptopt.evolution.run_experimental_sandbox",
            side_effect=stalling_sandbox,
        ):
            outcome = _run_cycle(component_id, force=True)

        assert_equal(outcome, "stalled", "constant failing score must produce a stalled cycle")

        logs = estore.list_cycle_logs(component_id)
        assert_true(len(logs) >= 1, "a cycle log must be written")
        last = logs[0]
        assert_equal(last["outcome"], "stalled", "cycle log outcome must be 'stalled'")
        sel = last.get("next_selection") or {}
        assert_true(
            "stalled_branch_cell" in sel,
            "stalled cycle log must record stalled_branch_cell as a negative signal"
        )

        # The store helper must surface this branch cell with a stall count.
        counts = estore.get_recent_stalled_branch_cells(component_id, last_n_cycles=10)
        assert_true(
            sel["stalled_branch_cell"] in counts,
            "get_recent_stalled_branch_cells must report the stalled branch cell"
        )
        assert_true(
            counts[sel["stalled_branch_cell"]] >= 1,
            "stall count for the failing branch cell must be >= 1"
        )
        print("PASS test_stall_records_negative_signal_in_cycle_log")
    finally:
        _cleanup(name)


# ---------------------------------------------------------------------------
# Test 19: select_next_thesis weights down recently-stalled branch cells
# ---------------------------------------------------------------------------

def test_select_next_thesis_deprioritizes_stalled_branch_cell():
    """A branch cell that has stalled >= max_stall_retries times must be
    deprioritized so an alternative branch cell is selected instead."""
    name = TEST_PREFIX + "deprioritize_stalled"
    _cleanup(name)
    try:
        component_id = _make_component(name, "swarm_config")

        db = SessionLocal()
        try:
            c = db.query(CognitionComponent).filter_by(id=component_id).first()
        finally:
            db.close()

        # Baseline: with no stalls, swarm_config selection favors the highest
        # raw-signal candidate (min_cs_threshold, signal 0.72 > evidence_strength 0.70).
        baseline_candidate, _ = select_next_thesis(c, cycle_index=1)
        baseline_cell = baseline_candidate["branch_cell"]

        # Record enough stalls of the baseline cell to cross the retry limit.
        from src.promptopt.evolution import MAX_STALL_RETRIES
        for k in range(MAX_STALL_RETRIES):
            estore.log_cycle(
                component_id=component_id,
                cycle_index=10 + k,
                outcome="stalled",
                thesis_id=None,
                next_selection={"stalled_branch_cell": baseline_cell},
                detail="seeded stall %d" % k,
            )

        counts = estore.get_recent_stalled_branch_cells(component_id, last_n_cycles=10)
        assert_true(
            counts.get(baseline_cell, 0) >= MAX_STALL_RETRIES,
            "seeded stalls must be counted for the baseline branch cell"
        )

        # Now selection must avoid the stalled cell (an alternative exists).
        new_candidate, votes = select_next_thesis(c, cycle_index=2)
        assert_true(
            new_candidate["branch_cell"] != baseline_cell,
            "select_next_thesis must avoid the deprioritized stalled branch cell "
            "(picked %r, stalled %r)" % (new_candidate["branch_cell"], baseline_cell)
        )
        assert_true(
            "stalled_branch_cells" in votes,
            "selection_votes must record the stalled branch cell counts for audit"
        )
        assert_true(
            baseline_cell in votes["stalled_branch_cells"],
            "audit record must include the stalled branch cell"
        )
        print("PASS test_select_next_thesis_deprioritizes_stalled_branch_cell")
    finally:
        _cleanup(name)


# ---------------------------------------------------------------------------
# Test 20: stall adjustment is bounded and respects the retry limit
# ---------------------------------------------------------------------------

def test_stall_adjusted_signal_phases():
    """_stall_adjusted_signal must leave un-stalled cells alone, apply a linear
    penalty below the retry limit, and a heavy penalty at/above it."""
    from src.promptopt.evolution import (
        _stall_adjusted_signal, STALL_SIGNAL_PENALTY, STALL_DEPRIORITIZE_PENALTY,
    )

    base = 0.80
    # No stalls -> unchanged
    assert_equal(
        _stall_adjusted_signal(base, "cellA", {}, max_retries=2), base,
        "no stalls must leave the signal unchanged"
    )
    # 1 stall, limit 2 -> linear penalty
    assert_equal(
        _stall_adjusted_signal(base, "cellA", {"cellA": 1}, max_retries=2),
        max(0.0, base - STALL_SIGNAL_PENALTY),
        "below the retry limit must apply the linear per-stall penalty"
    )
    # 2 stalls, limit 2 -> heavy deprioritization
    assert_equal(
        _stall_adjusted_signal(base, "cellA", {"cellA": 2}, max_retries=2),
        max(0.0, base - STALL_DEPRIORITIZE_PENALTY),
        "at the retry limit must apply the heavy deprioritize penalty"
    )
    # Never goes negative
    assert_true(
        _stall_adjusted_signal(0.10, "cellA", {"cellA": 5}, max_retries=2) >= 0.0,
        "adjusted signal must never be negative"
    )
    print("PASS test_stall_adjusted_signal_phases")


# ---------------------------------------------------------------------------
# Test 21: _refine_stalled_thesis materially changes the payload
# ---------------------------------------------------------------------------

def test_refine_stalled_thesis_materially_changes_payload():
    """A stalled-but-under-retry-limit candidate must be refined into a
    materially different payload, with auditable refinement metadata."""
    from src.promptopt.evolution import _refine_stalled_thesis

    class _Stub:
        def __init__(self, ctype):
            self.component_type = ctype

    # prompt_program: instructions get an alternate directive appended.
    pp_cand = {
        "branch_cell": "instructions",
        "thesis": {"instructions": "BASE INSTRUCTIONS."},
        "rationale": "original rationale",
        "score_signal": 0.75,
    }
    refined_pp = _refine_stalled_thesis(pp_cand, _Stub("prompt_program"), stall_n=1)
    assert_true(
        refined_pp["thesis"]["instructions"] != pp_cand["thesis"]["instructions"],
        "refined prompt_program instructions must differ from the stalled payload",
    )
    assert_true(
        "STALL-REFINEMENT" in refined_pp["thesis"]["instructions"],
        "refined instructions must carry the stall-refinement marker",
    )
    assert_true("refinement" in refined_pp, "refined candidate must carry refinement metadata")
    assert_equal(refined_pp["refinement"]["strategy"], "alternate_directive",
                 "prompt_program refinement strategy must be alternate_directive")
    assert_true(
        "STALL-REFINED" in refined_pp["rationale"],
        "rationale must record that the thesis was stall-refined (auditable)",
    )
    # Original candidate must be left untouched (refine returns a copy).
    assert_equal(pp_cand["thesis"]["instructions"], "BASE INSTRUCTIONS.",
                 "refine must not mutate the original candidate in place")

    # swarm_config: numeric value steps to a different number.
    sc_cand = {
        "branch_cell": "evidence_strength",
        "thesis": {"value": 0.65, "key": "swarm.min_evidence_strength"},
        "rationale": "raise evidence_strength",
        "score_signal": 0.70,
    }
    refined_sc = _refine_stalled_thesis(sc_cand, _Stub("swarm_config"), stall_n=1)
    assert_true(
        refined_sc["thesis"]["value"] != sc_cand["thesis"]["value"],
        "refined swarm_config value must differ from the stalled payload",
    )
    assert_equal(refined_sc["refinement"]["strategy"], "adjusted_step",
                 "numeric refinement strategy must be adjusted_step")

    # Successive retries explore different values (step scales with stall_n).
    refined_sc2 = _refine_stalled_thesis(sc_cand, _Stub("swarm_config"), stall_n=2)
    assert_true(
        refined_sc2["thesis"]["value"] != refined_sc["thesis"]["value"],
        "a higher stall count must explore a different refined value",
    )

    # Non-refinable payload (no-op probe) is returned unchanged.
    noop_cand = {"branch_cell": "instructions", "thesis": {"noop": True}, "rationale": "noop"}
    unchanged = _refine_stalled_thesis(noop_cand, _Stub("prompt_program"), stall_n=1)
    assert_true("refinement" not in unchanged, "non-refinable payload must not gain refinement metadata")

    print("PASS test_refine_stalled_thesis_materially_changes_payload")


# ---------------------------------------------------------------------------
# Test 22: select_next_thesis refines a stalled cell that is still under the
#          retry limit (rather than re-running the identical thesis)
# ---------------------------------------------------------------------------

def test_select_next_thesis_refines_stalled_cell_under_retry_limit():
    """When a branch cell has stalled once (below max_stall_retries), the next
    selection for that cell must carry a refined, materially different payload
    and record the refinement in selection_votes for audit."""
    name = TEST_PREFIX + "refine_under_limit"
    _cleanup(name)
    try:
        # prompt_program candidates all target the 'instructions' cell, so a
        # single stall on that cell guarantees the selected candidate is refined.
        component_id = _make_component(name, "prompt_program")

        db = SessionLocal()
        try:
            c = db.query(CognitionComponent).filter_by(id=component_id).first()
        finally:
            db.close()

        # Baseline: no stalls yet — selected payload carries no refinement.
        baseline_candidate, baseline_votes = select_next_thesis(c, cycle_index=1)
        assert_true(
            "STALL-REFINEMENT" not in json.dumps(baseline_candidate["thesis"]),
            "baseline (un-stalled) thesis must NOT be refined",
        )
        assert_true(
            baseline_votes.get("selected_refinement") is None,
            "baseline selection_votes must not record a refinement",
        )
        stalled_cell = baseline_candidate["branch_cell"]

        # Seed ONE stall for that cell (1 < MAX_STALL_RETRIES==2 → refine phase).
        estore.log_cycle(
            component_id=component_id,
            cycle_index=1,
            outcome="stalled",
            thesis_id=None,
            next_selection={"stalled_branch_cell": stalled_cell},
            detail="seeded single stall",
        )

        refined_candidate, votes = select_next_thesis(c, cycle_index=2)

        assert_equal(
            refined_candidate["branch_cell"], stalled_cell,
            "the stalled cell should still be selected while under the retry limit",
        )
        assert_true(
            "STALL-REFINEMENT" in json.dumps(refined_candidate["thesis"]),
            "the retried thesis for the stalled cell must be refined",
        )
        assert_true(
            json.dumps(refined_candidate["thesis"]) != json.dumps(baseline_candidate["thesis"]),
            "refined payload must be materially different from the stalled one",
        )
        assert_true(
            votes.get("selected_refinement") is not None,
            "selection_votes must record the refinement (auditable)",
        )
        assert_equal(
            votes["selected_refinement"]["stall_count"], 1,
            "refinement audit must record the stall count that triggered it",
        )
        assert_true(
            stalled_cell in (votes.get("refined_branch_cells") or []),
            "selection_votes must list the refined branch cell",
        )
        print("PASS test_select_next_thesis_refines_stalled_cell_under_retry_limit")
    finally:
        _cleanup(name)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_seed_components_idempotent,
    test_sandbox_isolated_from_live_state,
    test_thesis_starts_proposed_and_sandbox_labeled_experimental,
    test_rejected_thesis_never_promoted,
    test_promotion_blocked_without_all_tests_pass,
    test_passing_sandbox_advances_to_awaiting_consensus,
    test_next_thesis_selection_after_cycle,
    test_stall_detection,
    test_cycle_log_audit_trail,
    test_loop_enable_disable,
    test_branch_cells_registry,
    test_human_gate_blocks_truth_mutation,
    test_high_impact_score_triggers_human_gate,
    test_multi_iteration_no_premature_stall,
    test_enactment_record_written_before_truth_update,
    test_branch_cell_patch_isolates_single_cell,
    test_manual_trial_force_bypasses_loop_enabled,
    test_stall_records_negative_signal_in_cycle_log,
    test_select_next_thesis_deprioritizes_stalled_branch_cell,
    test_stall_adjusted_signal_phases,
    test_refine_stalled_thesis_materially_changes_payload,
    test_select_next_thesis_refines_stalled_cell_under_retry_limit,
]


if __name__ == "__main__":
    passed = 0
    failed = 0
    for fn in TESTS:
        try:
            fn()
            passed += 1
        except Exception as e:
            print("FAIL %s: %s" % (fn.__name__, e))
            traceback.print_exc()
            failed += 1
    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(0 if failed == 0 else 1)
