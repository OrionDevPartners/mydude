"""Tests for the self-evolving prompt engine + its governance guarantees.

Dual-mode coverage of ``src/promptopt/*`` and the governance promotion gate:

  Light path (no optimizer needed):
    * seed_default_programs is idempotent and produces a live, ever_live v1.
    * get_live_instructions reflects the DB per call (promotion/rollback flip it).
    * record_trace + count_usable_traces capture governed runs.
    * the min-trace gate blocks launch_run with too few usable traces (HTTP 400).
    * promotion ONLY takes effect through the governance enactment token
      (promote_prompt_version:<id>) — operator_enact flips live + archives prior.
    * rollback is a direct AUDITED revert restricted to previously-live versions;
      it refuses a never-live target (fail loud).

  Heavy path (DSPy, hermetic via an infinite DummyLM — no provider/network):
    * execute_run runs MIPROv2 then GEPA, persists candidate versions WITH scores
      and provenance, and completes the run row.
    * fail-loud: with no provider available and no injected LM, the run ends
      'failed' with a clear error — never a silent unverified prompt.

The engine is DB-backed, so the suite registers a throwaway test program, runs
against the real dev database, and cleans up its own rows afterwards.

Runnable two ways:
  * python tests/test_promptopt_governance.py   (standalone; exits non-zero on failure)
  * pytest tests/test_promptopt_governance.py    (test_* functions; no plugins needed)
"""
import itertools
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database import SessionLocal
from src.models import (
    GovernanceEnactment,
    PromptOptimizationRun,
    PromptProgram,
    PromptTrace,
    PromptVersion,
)
from src.promptopt import service, store
from src.promptopt import lm_bridge
from src.promptopt import signatures as sigs
from src.promptopt import specs as specs_mod
from src.promptopt.signatures import JudgeSynthesis
from src.promptopt.specs import ProgramSpec

TEST_PROGRAM = "__promptopt_test__"

GOOD_TEXT = (
    "RESULT: shipped\n"
    "ARTIFACTS: none\n"
    "CHECKS: all green\n"
    "RISKS: low — [VERIFIED] inputs validated\n"
    "CAPABILITIES: standard worker tools\n"
    "COMPRESSED_HANDOFF: task complete, no follow-up needed"
)


# --------------------------------------------------------------------------- #
# Registration + cleanup
# --------------------------------------------------------------------------- #

def _register_test_program():
    """Make TEST_PROGRAM optimizable by registering a spec + reusing the judge
    signature, so get_spec()/build_signature() resolve it like a real program."""
    spec = ProgramSpec(
        name=TEST_PROGRAM,
        signature_name="JudgeSynthesis",
        description="Throwaway program for prompt-engine governance tests.",
        seed_instructions=specs_mod.SEED_JUDGE_INSTRUCTIONS,
        input_fields=["user_request", "provider_outputs", "risk_directive"],
        output_field=specs_mod.JUDGE_OUTPUT_FIELD,
        required_sections=list(specs_mod.REQUIRED_SECTIONS),
    )
    specs_mod.PROGRAM_SPECS[TEST_PROGRAM] = spec
    sigs._SIGNATURES[TEST_PROGRAM] = JudgeSynthesis


def _cleanup():
    db = SessionLocal()
    try:
        prog = db.query(PromptProgram).filter_by(name=TEST_PROGRAM).first()
        if prog is not None:
            db.query(PromptTrace).filter_by(program_id=prog.id).delete()
            db.query(PromptOptimizationRun).filter_by(program_id=prog.id).delete()
            db.query(PromptVersion).filter_by(program_id=prog.id).delete()
            db.delete(prog)
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
    specs_mod.PROGRAM_SPECS.pop(TEST_PROGRAM, None)
    sigs._SIGNATURES.pop(TEST_PROGRAM, None)


def _program_id():
    db = SessionLocal()
    try:
        return db.query(PromptProgram).filter_by(name=TEST_PROGRAM).first().id
    finally:
        db.close()


def _version(version_id):
    db = SessionLocal()
    try:
        v = db.query(PromptVersion).filter_by(id=version_id).first()
        return None if v is None else (v.status, bool(v.ever_live), v.instructions)
    finally:
        db.close()


def _add_traces(n, text=GOOD_TEXT):
    pid = _program_id()
    vid, _, _ = store.get_live_instructions(TEST_PROGRAM)
    for i in range(n):
        rid = store.record_trace(
            TEST_PROGRAM, vid,
            {"user_request": "req %d" % i, "provider_outputs": "p%d CS=90 HR=0.1" % i,
             "risk_directive": ""},
            text,
            score_info={"score": 0.8, "compliance_score": 90, "hallucination_risk": 0.1},
            status="ok",
        )
        assert rid is not None, "record_trace returned None"
    return pid


# --------------------------------------------------------------------------- #
# Light tests
# --------------------------------------------------------------------------- #

def test_seed_idempotent_and_live():
    store.seed_default_programs()
    store.seed_default_programs()  # second call must not create a second v1
    vid, instr, demos = store.get_live_instructions(TEST_PROGRAM)
    assert vid is not None
    assert "MERGER/JUDGE" in instr
    status, ever_live, _ = _version(vid)
    assert status == "live" and ever_live is True
    db = SessionLocal()
    try:
        pid = _program_id()
        assert db.query(PromptVersion).filter_by(program_id=pid).count() == 1
    finally:
        db.close()


def test_promotion_requires_governance_token():
    """A candidate goes live ONLY via the promote_prompt_version enactment token."""
    from src.swarm.governance_engine import GovernanceEngine

    base_vid, _, _ = store.get_live_instructions(TEST_PROGRAM)
    pid = _program_id()
    cand_text = "EVOLVED INSTRUCTIONS v2\n" + specs_mod.SEED_JUDGE_INSTRUCTIONS
    cand_id = store.create_candidate_version(
        pid, cand_text, [], {"optimizer": "GEPA", "base_score": 0.7, "delta": 0.1},
        score=0.8, optimization_run_id=None,
    )
    # still a candidate; live is unchanged
    assert _version(cand_id)[0] == "candidate"
    assert store.get_live_instructions(TEST_PROGRAM)[0] == base_vid

    res = store.propose_promotion(cand_id)
    assert res["proposal_db_id"], "propose_promotion did not return a proposal id"
    # proposal raised but NOT yet enacted -> live unchanged (the gate holds)
    assert store.get_live_instructions(TEST_PROGRAM)[0] == base_vid
    assert _version(cand_id)[0] == "candidate"

    ok = GovernanceEngine().operator_enact(res["proposal_db_id"], operator="tester")
    assert ok is True, "operator_enact failed"

    # now the token took effect: candidate live, prior archived
    live_vid, live_instr, _ = store.get_live_instructions(TEST_PROGRAM)
    assert live_vid == cand_id
    assert "EVOLVED INSTRUCTIONS v2" in live_instr
    assert _version(cand_id)[0] == "live"
    assert _version(base_vid)[0] == "archived"
    assert _version(base_vid)[1] is True  # base remains ever_live for rollback


def test_audited_rollback_to_previous_live():
    base_vid, _, _ = None, None, None
    db = SessionLocal()
    try:
        pid = _program_id()
        # v1 is the only ever_live, non-live version after the promotion test
        v1 = (db.query(PromptVersion)
              .filter_by(program_id=pid, version_no=1).first())
        base_vid = v1.id
    finally:
        db.close()

    before = _enactment_count()
    res = store.rollback_to(base_vid, operator="tester")
    assert res["version_id"] == base_vid
    live_vid, live_instr, _ = store.get_live_instructions(TEST_PROGRAM)
    assert live_vid == base_vid
    assert "MERGER/JUDGE" in live_instr
    assert _enactment_count() == before + 1, "rollback must write an audit row"


def test_rollback_rejects_never_live():
    pid = _program_id()
    never_live_id = store.create_candidate_version(
        pid, "NEVER LIVE", [], {"optimizer": "MIPROv2"}, score=0.5, optimization_run_id=None,
    )
    assert _version(never_live_id)[1] is False
    raised = False
    try:
        store.rollback_to(never_live_id, operator="tester")
    except ValueError as e:
        raised = "never live" in str(e)
    assert raised, "rollback to a never-live version must fail loud"


def test_min_trace_gate():
    # at this point the program has 0 usable traces -> launch must refuse
    db = SessionLocal()
    try:
        pid = _program_id()
        assert store.count_usable_traces(db, pid) < service.min_traces()
    finally:
        db.close()
    raised = False
    try:
        service.launch_run(TEST_PROGRAM, started_by="tester")
    except service.NotEnoughTraces:
        raised = True
    assert raised, "launch_run must raise NotEnoughTraces below the threshold"


def test_trace_capture_counts():
    pid = _program_id()
    db = SessionLocal()
    try:
        before = store.count_usable_traces(db, pid)
    finally:
        db.close()
    _add_traces(3)
    db = SessionLocal()
    try:
        assert store.count_usable_traces(db, pid) == before + 3
    finally:
        db.close()


def _enactment_count():
    db = SessionLocal()
    try:
        return db.query(GovernanceEnactment).count()
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Heavy tests (DSPy + infinite DummyLM)
# --------------------------------------------------------------------------- #

_PROPOSED_INSTRUCTION = (
    "You are the MERGER/JUDGE. Weight providers by compliance score and synthesize "
    "one consolidated answer that includes EVERY section header exactly: RESULT, "
    "ARTIFACTS, CHECKS, RISKS, CAPABILITIES, COMPRESSED_HANDOFF."
)


def _dummy_lm():
    import dspy
    # Non-list iterable -> DummyLM never exhausts; every call returns the same
    # superset dict. The judge program reads `consolidated_response`; MIPROv2's
    # meta-proposer reads `proposed_instruction`/`observations`/etc. ChatAdapter
    # ignores the extra field blocks, so one dict satisfies every call type. This
    # keeps the optimization run hermetic (no provider/network) while the metric
    # still genuinely scores the produced outputs.
    answer = {
        specs_mod.JUDGE_OUTPUT_FIELD: GOOD_TEXT,
        "proposed_instruction": _PROPOSED_INSTRUCTION,
        "observations": "Dataset asks the judge to merge provider outputs into the six sections.",
        "summary": "Merge provider outputs into a governed worker-format answer.",
        "program_description": "Synthesize provider outputs into one consolidated answer.",
        "module_description": "Judge module merging provider outputs.",
        "rationale": "All required sections present and compliant.",
        "reasoning": "Include every required section header.",
    }
    return dspy.utils.DummyLM(itertools.repeat(answer))


def _new_run_row():
    db = SessionLocal()
    try:
        pid = _program_id()
        run = PromptOptimizationRun(
            program_id=pid, optimizer="mipro+gepa", status="running",
            trainset_size=0, started_by="tester",
        )
        db.add(run)
        db.commit()
        return run.id
    finally:
        db.close()


def _run_status(run_id):
    db = SessionLocal()
    try:
        r = db.query(PromptOptimizationRun).filter_by(id=run_id).first()
        return None if r is None else (r.status, r.error, r.base_score, r.best_score,
                                       json.loads(r.candidates_json or "[]"))
    finally:
        db.close()


def test_optimization_with_dummy_lm_produces_candidates():
    # ensure enough usable traces for a real trainset/valset split
    _add_traces(max(0, service.min_traces() + 2))
    pid = _program_id()
    before_versions = _version_count(pid)

    run_id = _new_run_row()
    budgets = {
        "mipro_auto": "light", "num_threads": 1,
        "gepa_max_metric_calls": 4, "gepa_minibatch": 2, "max_tokens": 200,
        "trace_limit": 50,
    }
    service.execute_run(run_id, TEST_PROGRAM, budgets=budgets, lm=_dummy_lm())

    status, error, base, best, candidates = _run_status(run_id)
    assert status == "completed", "optimization run failed: %s" % error
    assert candidates, "no candidate versions persisted"
    for c in candidates:
        assert c["score"] is not None, "candidate missing score"
        assert c["version_id"], "candidate missing version id"
    assert best is not None
    assert _version_count(pid) > before_versions, "no new candidate versions created"


def test_optimization_fail_loud_without_provider():
    _add_traces(max(0, service.min_traces() + 2))
    run_id = _new_run_row()
    real = lm_bridge.available_provider
    lm_bridge.available_provider = lambda: None  # force no-provider, hermetic
    try:
        service.execute_run(run_id, TEST_PROGRAM, budgets={"trace_limit": 50}, lm=None)
    finally:
        lm_bridge.available_provider = real
    status, error, _, _, _ = _run_status(run_id)
    assert status == "failed", "run should fail loud with no provider"
    assert "provider" in (error or "").lower(), "error must name the missing provider"


def _version_count(pid):
    db = SessionLocal()
    try:
        return db.query(PromptVersion).filter_by(program_id=pid).count()
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #

# Ordered so DB state flows correctly (seed -> promote -> rollback -> traces -> optimize).
_TESTS = [
    test_seed_idempotent_and_live,
    test_promotion_requires_governance_token,
    test_audited_rollback_to_previous_live,
    test_rollback_rejects_never_live,
    test_min_trace_gate,
    test_trace_capture_counts,
    test_optimization_with_dummy_lm_produces_candidates,
    test_optimization_fail_loud_without_provider,
]


def main():
    _cleanup()
    _register_test_program()
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
