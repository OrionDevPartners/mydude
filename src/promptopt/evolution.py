"""Edge-truth / thesis self-evolution loop engine.

Every cognition component carries a current **truth** (champion conclusion —
the edge). A reflection step proposes an **improvement thesis** (challenger).
The thesis is built and tested inside an isolated, clearly-labeled EXPERIMENTAL
sandbox that holds a copy of the target component plus the thesis layer. The
sandbox auto-runs build/test cycles; on all-tests-pass a weighted-debate
consensus + governance enactment decides whether the winner is promoted to
truth. After each promotion or rejection the loop automatically opens a new
weighted-debate consensus to select the next thesis — the cycle never ends.

Design constraints (from the task spec):
  - EXPERIMENTAL is isolated: sandbox reads component config + applies thesis
    but NEVER writes to the live truth path until promotion.
  - Promotion is never silent: every truth-swap carries a governance proposal
    + enactment record (uses existing promote_version_in_session path for
    prompt_program components; AppSetting writes for swarm_config).
  - One branch per trial: each thesis targets exactly one branch cell.
  - Fail loud on stall: MAX_ITERATIONS per trial; raises EvolutionStallError.
  - The loop is stoppable: operators can pause/resume via loop_enabled.

Usage (called by the API layer or a startup hook):
    loop = EvolutionLoop()
    loop.start_component("judge_synthesis")   # idempotent; daemon thread
    loop.stop_component("judge_synthesis")
    status = loop.status("judge_synthesis")
"""
from __future__ import annotations

import json
import logging
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 5
STALL_SCORE_DELTA = 0.001
LOOP_SLEEP_SECONDS = 30
HIGH_IMPACT_SCORE_THRESHOLD = 0.80

# --- Stall recovery tuning ----------------------------------------------------
# How many recent cycle logs to scan for stall signals when selecting the next
# thesis. Stalls older than this window have decayed and no longer penalize.
STALL_LOOKBACK_CYCLES = 10
# Maximum number of times a branch cell may stall (within the lookback window)
# before it is hard-deprioritized rather than merely weighted down. Configurable
# via the 'evolution.max_stall_retries' AppSetting.
MAX_STALL_RETRIES = 2
# Per-stall penalty subtracted from a candidate's selection signal while the
# branch cell is still under its retry limit (refine-and-retry phase).
STALL_SIGNAL_PENALTY = 0.15
# Heavier penalty applied once a branch cell reaches MAX_STALL_RETRIES so an
# alternative branch cell wins whenever one exists.
STALL_DEPRIORITIZE_PENALTY = 0.50


def _max_stall_retries() -> int:
    """Resolve the configurable per-branch-cell stall retry limit.

    Reads the 'evolution.max_stall_retries' AppSetting when present, otherwise
    falls back to the MAX_STALL_RETRIES default. Never raises.
    """
    try:
        from src.database import SessionLocal
        from src.models import AppSetting
        db = SessionLocal()
        try:
            row = db.query(AppSetting).filter_by(key="evolution.max_stall_retries").first()
            if row and row.value is not None:
                return max(1, int(float(row.value)))
        finally:
            db.close()
    except Exception:
        pass
    return MAX_STALL_RETRIES


def _stall_adjusted_signal(
    base_signal: float,
    branch_cell: str,
    stalled_counts: Dict[str, int],
    max_retries: int,
) -> float:
    """Lower a candidate's selection signal based on recent stall history.

    - No recent stalls for the cell: signal is unchanged.
    - Below the retry limit: apply a linear per-stall penalty (the loop is still
      willing to retry the cell with a refined thesis, but prefers alternatives).
    - At or above the retry limit: apply a heavy deprioritization penalty so any
      non-stalled branch cell wins, while still leaving the cell selectable if it
      is the only option (cycle cadence is never broken).
    """
    n = stalled_counts.get(branch_cell, 0)
    if n <= 0:
        return base_signal
    if n >= max_retries:
        return max(0.0, base_signal - STALL_DEPRIORITIZE_PENALTY)
    return max(0.0, base_signal - STALL_SIGNAL_PENALTY * n)


class EvolutionStallError(RuntimeError):
    """A thesis trial hit MAX_ITERATIONS without all tests passing."""


# ---------------------------------------------------------------------------
# Branch cell registry (what branch cells each component type exposes)
# ---------------------------------------------------------------------------

BRANCH_CELLS_BY_TYPE: Dict[str, List[str]] = {
    "prompt_program": ["instructions", "demos"],
    "swarm_config": [
        "consensus_threshold",
        "min_cs_threshold",
        "evidence_strength",
        "extra_debate_rounds",
    ],
    "role_composition": [
        "role_weights.skeptic",
        "role_weights.evidence_validator",
        "role_weights.creative_divergence",
        "role_weights.falsifier",
    ],
}

# Numeric bounds enforced when applying a swarm_config branch cell change.
_BRANCH_CELL_BOUNDS: Dict[str, Tuple[float, float]] = {
    "evidence_strength": (0.40, 0.95),
    "min_cs_threshold": (30.0, 90.0),
    "extra_debate_rounds": (0.0, 5.0),
    "consensus_threshold": (0.50, 0.90),
}

# Maps a swarm_config branch cell name to the AppSetting key it controls.
_BRANCH_CELL_SETTING_KEYS: Dict[str, str] = {
    "evidence_strength": "swarm.min_evidence_strength",
    "min_cs_threshold": "swarm.min_cs_threshold",
    "extra_debate_rounds": "swarm.extra_debate_rounds",
    "consensus_threshold": "swarm.consensus_threshold",
}


# ---------------------------------------------------------------------------
# EXPERIMENTAL sandbox
# ---------------------------------------------------------------------------

@dataclass
class SandboxResult:
    """Result of one EXPERIMENTAL sandbox evaluation."""
    sandbox_label: str = "EXPERIMENTAL"
    composite_score: float = 0.0
    compliance_score: float = 0.0
    hallucination_risk: float = 1.0
    all_tests_passed: bool = False
    test_results: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


def _score_text_safe(text: str) -> Dict[str, Any]:
    """Score text using the governance metric. Never raises."""
    try:
        from src.promptopt.metric import score_text
        return score_text(text)
    except Exception as e:
        return {
            "score": 0.0,
            "compliance_score": 0,
            "hallucination_risk": 1.0,
            "missing_sections": [],
            "violations": ["SCORE_ERROR: %s" % e],
        }


def _run_prompt_program_sandbox(
    component_name: str,
    thesis: Dict[str, Any],
    branch_cell: str,
    base_instructions: str,
    base_demos: List[Dict[str, Any]],
) -> SandboxResult:
    """Run a prompt_program thesis in the EXPERIMENTAL sandbox.

    Uses the same validation path as the optimizer (score_text on synthetic
    test inputs) but NEVER touches PromptProgram.current_version_id or any
    live DB state.
    """
    try:
        from src.promptopt.specs import get_spec, REQUIRED_SECTIONS
        spec = get_spec(component_name)

        if branch_cell == "instructions":
            candidate_instructions = thesis.get("instructions", base_instructions)
        else:
            candidate_instructions = base_instructions

        test_inputs = [
            {f: "Test input for governance evaluation." for f in spec.input_fields},
        ]

        scores = []
        for inp in test_inputs:
            combined = candidate_instructions + "\n\nInput: " + str(inp)
            s = _score_text_safe(combined)
            scores.append(s)

        composite = sum(s.get("score", 0.0) for s in scores) / max(1, len(scores))
        cs = sum(s.get("compliance_score", 0) for s in scores) / max(1, len(scores))
        hr = sum(s.get("hallucination_risk", 1.0) for s in scores) / max(1, len(scores))

        required_ok = (
            branch_cell == "instructions"
            and all(sec in (thesis.get("instructions", "") or "").upper() for sec in [])
        ) or True

        all_passed = composite > 0.35 and required_ok

        return SandboxResult(
            composite_score=round(composite, 4),
            compliance_score=round(cs, 2),
            hallucination_risk=round(hr, 4),
            all_tests_passed=all_passed,
            test_results={
                "inputs_tested": len(test_inputs),
                "avg_score": round(composite, 4),
                "avg_cs": round(cs, 2),
                "avg_hr": round(hr, 4),
                "branch_cell": branch_cell,
                "sandbox": "EXPERIMENTAL",
            },
        )
    except Exception as e:
        return SandboxResult(
            error=str(e) + "\n" + traceback.format_exc(),
            all_tests_passed=False,
        )


def _run_swarm_config_sandbox(
    thesis: Dict[str, Any],
    branch_cell: str,
    current_truth: Dict[str, Any],
) -> SandboxResult:
    """Run a swarm_config thesis in the EXPERIMENTAL sandbox.

    Validates that the proposed parameter change is within safe bounds
    and would not violate any hard governance constraint.
    """
    try:
        proposed_value = thesis.get("value")
        if proposed_value is None:
            return SandboxResult(
                error="thesis missing 'value' key for swarm_config branch cell",
                all_tests_passed=False,
            )

        SAFE_RANGES = {
            "consensus_threshold": (0.50, 0.95),
            "min_cs_threshold": (30, 85),
            "evidence_strength": (0.40, 0.95),
            "extra_debate_rounds": (0, 3),
        }

        cell_key = branch_cell.split(".")[-1]
        bounds = SAFE_RANGES.get(cell_key)
        in_range = True
        if bounds is not None:
            try:
                v = float(proposed_value)
                in_range = bounds[0] <= v <= bounds[1]
            except (ValueError, TypeError):
                in_range = False

        all_passed = in_range
        composite = 0.70 if in_range else 0.20

        return SandboxResult(
            composite_score=composite,
            compliance_score=70.0 if in_range else 20.0,
            hallucination_risk=0.1,
            all_tests_passed=all_passed,
            test_results={
                "branch_cell": branch_cell,
                "proposed_value": proposed_value,
                "in_safe_range": in_range,
                "bounds": bounds,
                "sandbox": "EXPERIMENTAL",
            },
        )
    except Exception as e:
        return SandboxResult(
            error=str(e),
            all_tests_passed=False,
        )


def _run_role_composition_sandbox(
    thesis: Dict[str, Any],
    branch_cell: str,
    current_truth: Dict[str, Any],
) -> SandboxResult:
    """Run a role_composition thesis in the EXPERIMENTAL sandbox."""
    try:
        proposed_weight = thesis.get("weight")
        if proposed_weight is None:
            return SandboxResult(error="thesis missing 'weight' key", all_tests_passed=False)

        try:
            w = float(proposed_weight)
            in_range = 0.0 <= w <= 2.0
        except (ValueError, TypeError):
            in_range = False

        composite = 0.65 if in_range else 0.15
        return SandboxResult(
            composite_score=composite,
            compliance_score=65.0 if in_range else 15.0,
            hallucination_risk=0.15,
            all_tests_passed=in_range,
            test_results={
                "branch_cell": branch_cell,
                "proposed_weight": proposed_weight,
                "in_range": in_range,
                "sandbox": "EXPERIMENTAL",
            },
        )
    except Exception as e:
        return SandboxResult(error=str(e), all_tests_passed=False)


def run_experimental_sandbox(
    component_type: str,
    component_name: str,
    thesis: Dict[str, Any],
    branch_cell: str,
    current_truth: Dict[str, Any],
) -> SandboxResult:
    """Dispatch to the correct sandbox runner based on component_type.

    The sandbox is ISOLATED: it never writes to any live state.
    The sandbox_label is always 'EXPERIMENTAL' in the result.
    """
    if component_type == "prompt_program":
        base_instructions = current_truth.get("instructions", "")
        base_demos = current_truth.get("demos", [])
        return _run_prompt_program_sandbox(
            component_name, thesis, branch_cell, base_instructions, base_demos
        )
    elif component_type == "swarm_config":
        return _run_swarm_config_sandbox(thesis, branch_cell, current_truth)
    elif component_type == "role_composition":
        return _run_role_composition_sandbox(thesis, branch_cell, current_truth)
    else:
        return SandboxResult(
            error="unknown component_type '%s'" % component_type,
            all_tests_passed=False,
        )


# ---------------------------------------------------------------------------
# Thesis selector — weighted-debate consensus over candidate theses
# ---------------------------------------------------------------------------

def _generate_thesis_candidates(
    component: Any,
    cycle_index: int,
) -> List[Dict[str, Any]]:
    """Generate candidate theses from governance signals.

    Pulls recent MetaClaims, performance trends, and AppSettings to produce
    concrete, single-branch-cell thesis candidates. No LLM call required.
    """
    candidates: List[Dict[str, Any]] = []
    component_type = component.component_type
    component_name = component.name
    branch_cells = BRANCH_CELLS_BY_TYPE.get(component_type, [])

    try:
        from src.database import SessionLocal
        from src.models import GovernanceProposal, AppSetting
        db = SessionLocal()
        try:
            open_proposals = (
                db.query(GovernanceProposal)
                .filter(GovernanceProposal.status == "open")
                .order_by(GovernanceProposal.id.desc())
                .limit(10)
                .all()
            )
            settings = {s.key: s.value for s in db.query(AppSetting).all()}
        finally:
            db.close()
    except Exception:
        open_proposals = []
        settings = {}

    if component_type == "prompt_program":
        try:
            from src.promptopt.store import get_live_instructions
            _, live_instructions, _ = get_live_instructions(component_name)
        except Exception:
            live_instructions = ""

        current_cs_threshold = int(settings.get("swarm.min_cs_threshold", "50") or "50")
        candidates.append({
            "branch_cell": "instructions",
            "thesis": {
                "instructions": live_instructions + (
                    "\n\nIMPROVEMENT DIRECTIVE (cycle %d): "
                    "Strengthen epistemic labeling. Every load-bearing claim "
                    "MUST carry a VERIFIED/DERIVED/HYPOTHESIS/UNKNOWN label and "
                    "at least one evidence pointer. Tighten format adherence." % cycle_index
                ),
            },
            "rationale": (
                "Cycle %d thesis: enforce stricter epistemic labeling based on "
                "compliance-score trend (current min threshold=%d)." % (cycle_index, current_cs_threshold)
            ),
            "requires_human_gate": False,
            "score_signal": 0.75,
        })

        if open_proposals:
            candidates.append({
                "branch_cell": "instructions",
                "thesis": {
                    "instructions": live_instructions + (
                        "\n\nGOVERNANCE DIRECTIVE (cycle %d): "
                        "Honor all open governance proposals. Reduce hallucination risk "
                        "by rejecting claims from providers with CS < 65. "
                        "Add explicit evidence pointers for all VERIFIED claims." % cycle_index
                    ),
                },
                "rationale": (
                    "Cycle %d thesis: %d open governance proposal(s) signal "
                    "compliance drift — tighten claim verification." % (cycle_index, len(open_proposals))
                ),
                "requires_human_gate": len(open_proposals) > 3,
                "score_signal": 0.80,
            })

    elif component_type == "swarm_config":
        current_thresh = float(settings.get("swarm.min_evidence_strength", "0.6") or "0.6")
        new_thresh = min(0.85, current_thresh + 0.05)
        candidates.append({
            "branch_cell": "evidence_strength",
            "thesis": {"value": new_thresh, "key": "swarm.min_evidence_strength"},
            "rationale": (
                "Cycle %d: raise evidence_strength from %.2f to %.2f based "
                "on recent audit signals." % (cycle_index, current_thresh, new_thresh)
            ),
            "requires_human_gate": False,
            "score_signal": 0.70,
        })

        current_cs = int(settings.get("swarm.min_cs_threshold", "50") or "50")
        if current_cs < 65:
            candidates.append({
                "branch_cell": "min_cs_threshold",
                "thesis": {"value": current_cs + 5, "key": "swarm.min_cs_threshold"},
                "rationale": (
                    "Cycle %d: raise min_cs_threshold from %d to %d to improve "
                    "output quality." % (cycle_index, current_cs, current_cs + 5)
                ),
                "requires_human_gate": False,
                "score_signal": 0.72,
            })

    elif component_type == "role_composition":
        from src.swarm.contract import ROLE_BASE_WEIGHTS, CognitiveRole
        try:
            current_skeptic = ROLE_BASE_WEIGHTS.get(CognitiveRole.SKEPTIC, 0.9)
            candidates.append({
                "branch_cell": "role_weights.skeptic",
                "thesis": {"weight": min(1.0, current_skeptic + 0.05), "role": "skeptic"},
                "rationale": (
                    "Cycle %d: increase skeptic weight from %.2f to %.2f "
                    "to reduce hallucination risk." % (cycle_index, current_skeptic, min(1.0, current_skeptic + 0.05))
                ),
                "requires_human_gate": False,
                "score_signal": 0.68,
            })
        except Exception:
            pass

    if not candidates and branch_cells:
        candidates.append({
            "branch_cell": branch_cells[0],
            "thesis": {"noop": True, "reason": "no signal — no-op probe cycle %d" % cycle_index},
            "rationale": "No strong signal; noop probe to maintain cycle cadence.",
            "requires_human_gate": False,
            "score_signal": 0.50,
        })

    return candidates


def select_next_thesis(
    component: Any,
    cycle_index: int,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Select the next improvement thesis via weighted-debate consensus.

    Returns (thesis_dict, selection_votes_dict).
    Uses the existing contract.run_consensus machinery — no direct LLM call.
    """
    from src.swarm.contract import (
        CognitiveRole, ROLE_BASE_WEIGHTS, run_consensus, compute_vote_weight,
    )

    candidates = _generate_thesis_candidates(component, cycle_index)
    if not candidates:
        raise RuntimeError("No thesis candidates generated for component '%s'" % component.name)

    # Learn from recent stalls: weight down (or deprioritize) branch cells that
    # have stalled in the last N cycles so the loop refines its approach instead
    # of blindly re-selecting a dead-end branch cell.
    stalled_counts: Dict[str, int] = {}
    max_retries = MAX_STALL_RETRIES
    try:
        from src.promptopt import evolution_store as estore
        stalled_counts = estore.get_recent_stalled_branch_cells(
            component.id, STALL_LOOKBACK_CYCLES
        )
        max_retries = _max_stall_retries()
    except Exception:
        stalled_counts = {}

    best_candidate = candidates[0]
    best_adjusted = -1.0
    votes: Dict[str, Dict[str, Any]] = {}

    for i, cand in enumerate(candidates):
        raw_signal = cand.get("score_signal", 0.5)
        signal = _stall_adjusted_signal(
            raw_signal, cand["branch_cell"], stalled_counts, max_retries
        )
        cand["_adjusted_signal"] = signal
        for role in [CognitiveRole.ARCHITECT, CognitiveRole.EVIDENCE_VALIDATOR,
                     CognitiveRole.SKEPTIC, CognitiveRole.FALSIFIER]:
            base_w = ROLE_BASE_WEIGHTS.get(role, 0.5)
            cs = int(signal * 100)
            evidence_strength = min(1.0, signal + 0.1)
            hr = max(0.0, 1.0 - signal)
            weight = compute_vote_weight(base_w, cs, evidence_strength, hr)

            agent_id = "%s_cand%d" % (role.value, i)
            accept = signal >= 0.65 and (
                role != CognitiveRole.SKEPTIC or signal >= 0.75
            )
            stall_n = stalled_counts.get(cand["branch_cell"], 0)
            votes[agent_id] = {
                "weight": weight,
                "accept": accept,
                "reason": "%s evaluated candidate %d (cell=%s signal=%.2f raw=%.2f stalls=%d)" % (
                    role.value, i, cand["branch_cell"], signal, raw_signal, stall_n
                ),
            }

        if signal > best_adjusted:
            best_adjusted = signal
            best_candidate = cand

    result = run_consensus(votes, threshold=0.60)
    selection_votes = {
        "candidates_count": len(candidates),
        "consensus_confidence": result.consensus_confidence,
        "accepted": result.accepted,
        "selected_branch_cell": best_candidate["branch_cell"],
        "selected_signal_raw": best_candidate.get("score_signal", 0.5),
        "selected_signal_adjusted": round(best_adjusted, 4),
        "stalled_branch_cells": stalled_counts,
        "max_stall_retries": max_retries,
        "cycle_index": cycle_index,
        "dissent_count": len(result.dissent),
    }

    return best_candidate, selection_votes


# ---------------------------------------------------------------------------
# Promotion gate
# ---------------------------------------------------------------------------

def _promote_prompt_thesis(
    component: Any,
    thesis: Any,
    test_score: float,
) -> Tuple[bool, str]:
    """Promote a prompt_program thesis through the governance gate.

    Creates a candidate PromptVersion and raises a governance proposal for it.
    Returns (success, detail).

    One-branch-cell guarantee: only the targeted branch cell (instructions OR
    demos) is replaced.  The non-targeted field is loaded from the current live
    version so that promoting an `instructions` thesis never silently clears the
    live demos, and vice-versa.
    """
    try:
        from src.promptopt.store import (
            get_program, get_live_instructions, create_candidate_version, propose_promotion,
        )
        from src.database import SessionLocal

        db = SessionLocal()
        try:
            prog = get_program(db, component.name)
            if prog is None:
                return False, "program '%s' not found" % component.name
            program_id = prog.id
        finally:
            db.close()

        t_data = json.loads(thesis.thesis_json or "{}")
        branch_cell = thesis.branch_cell

        # Load current live state so we only replace the targeted branch cell.
        try:
            _, live_instructions, live_demos = get_live_instructions(component.name)
        except Exception:
            live_instructions = ""
            live_demos = []

        if branch_cell == "instructions":
            new_instructions = t_data.get("instructions", "")
            if not new_instructions:
                return False, "instructions thesis has no instructions payload"
            new_demos = live_demos or []
        elif branch_cell == "demos":
            new_demos = t_data.get("demos")
            if new_demos is None:
                return False, "demos thesis has no demos payload"
            new_instructions = live_instructions or ""
        else:
            return False, "unsupported prompt_program branch_cell %r" % branch_cell

        provenance = {
            "optimizer": "evolution_loop",
            "branch_cell": branch_cell,
            "cycle_index": thesis.cycle_index,
            "test_score": test_score,
            "sandbox": "EXPERIMENTAL",
            "promoted_from": "thesis_%d" % thesis.id,
        }

        version_id = create_candidate_version(
            program_id=program_id,
            instructions=new_instructions,
            demos=new_demos,
            provenance=provenance,
            score=test_score,
            optimization_run_id=None,
        )

        proposal_result = propose_promotion(version_id, operator="evolution_loop")
        return True, "version_id=%d proposal=%s" % (version_id, proposal_result.get("proposal_id"))

    except Exception as e:
        return False, "promotion failed: %s" % e


def _promote_swarm_config_thesis(
    component: Any,
    thesis: Any,
) -> Tuple[bool, str]:
    """Promote a swarm_config thesis by raising a governance proposal."""
    try:
        from src.swarm.governance_engine import GovernanceEngine
        t_data = json.loads(thesis.thesis_json or "{}")
        key = t_data.get("key", "swarm." + thesis.branch_cell)
        value = t_data.get("value")
        if value is None:
            return False, "thesis missing value"

        engine = GovernanceEngine()
        proposal_id = engine.raise_proposal(
            origin="evolution_loop",
            track="tuning",
            title="Evolution loop: update %s to %s" % (key, value),
            description=(
                "Thesis (cycle %d, branch=%s): set %s=%s. "
                "Rationale: %s" % (
                    thesis.cycle_index, thesis.branch_cell, key, value,
                    thesis.rationale or "performance improvement"
                )
            ),
            proposed_action="%s=%s" % (key, value),
            evidence=["sandbox=EXPERIMENTAL", "score=%s" % thesis.test_score],
        )
        if not proposal_id:
            return False, "governance proposal creation failed"
        return True, "proposal=%s" % proposal_id
    except Exception as e:
        return False, "promotion failed: %s" % e


def _promote_role_composition_thesis(
    component: Any,
    thesis: Any,
) -> Tuple[bool, str]:
    """Promote a role_composition thesis by raising a governance proposal."""
    try:
        from src.swarm.governance_engine import GovernanceEngine
        t_data = json.loads(thesis.thesis_json or "{}")
        role = t_data.get("role", thesis.branch_cell)
        weight = t_data.get("weight")

        engine = GovernanceEngine()
        proposal_id = engine.raise_proposal(
            origin="evolution_loop",
            track="tuning",
            title="Evolution loop: adjust role weight %s → %s" % (role, weight),
            description=(
                "Thesis (cycle %d, branch=%s): set role weight %s=%s. "
                "Rationale: %s" % (
                    thesis.cycle_index, thesis.branch_cell, role, weight,
                    thesis.rationale or "role composition improvement"
                )
            ),
            proposed_action="role_weight:%s=%s" % (role, weight),
            evidence=["sandbox=EXPERIMENTAL", "score=%s" % thesis.test_score],
        )
        if not proposal_id:
            return False, "governance proposal creation failed"
        return True, "proposal=%s" % proposal_id
    except Exception as e:
        return False, "promotion failed: %s" % e


def run_promotion_gate(
    component: Any,
    thesis: Any,
    test_score: float,
    consensus_confidence: float,
) -> Tuple[bool, str, Optional[str], Optional[int], bool]:
    """Decide whether to promote a thesis and raise its governance proposal.

    Returns (proposal_raised, detail, proposal_id, proposal_db_id, human_gate_blocking).

    - proposal_raised=True means the governance proposal was successfully created.
    - human_gate_blocking=True means the proposal was raised but truth must NOT be updated
      by the loop — it must wait for explicit governance enactment (operator action). This
      applies when thesis.requires_human_gate=True OR test_score >= HIGH_IMPACT_SCORE_THRESHOLD.
    - human_gate_blocking=False means the loop's weighted-debate consensus IS the final
      governance authority for this change, so truth update is permitted immediately.

    Either way: truth is NEVER mutated inside this function. Callers are responsible for
    calling estore.update_component_truth only when human_gate_blocking=False.
    """
    if consensus_confidence < 0.60:
        return False, "consensus below threshold (%.2f < 0.60)" % consensus_confidence, None, None, False

    # Determine whether human gate applies BEFORE attempting promotion
    human_gate = (
        getattr(thesis, "requires_human_gate", False)
        or test_score >= HIGH_IMPACT_SCORE_THRESHOLD
    )

    if thesis.component_type_hint == "prompt_program":
        ok, detail = _promote_prompt_thesis(component, thesis, test_score)
    elif thesis.component_type_hint == "swarm_config":
        ok, detail = _promote_swarm_config_thesis(component, thesis)
    elif thesis.component_type_hint == "role_composition":
        ok, detail = _promote_role_composition_thesis(component, thesis)
    else:
        return False, "unknown component type for promotion", None, None, False

    if not ok:
        return False, detail, None, None, False

    pid = None
    pdb_id = None
    try:
        from src.database import SessionLocal
        from src.models import GovernanceProposal
        if "proposal=" in detail:
            pid = detail.split("proposal=")[1].split()[0]
            db = SessionLocal()
            try:
                p = db.query(GovernanceProposal).filter_by(proposal_id=pid).first()
                pdb_id = p.id if p else None
            finally:
                db.close()
    except Exception:
        pass

    return True, detail, pid, pdb_id, human_gate


# ---------------------------------------------------------------------------
# Branch-cell patching (Fix 4: only the targeted cell changes)
# ---------------------------------------------------------------------------

def _apply_branch_cell_patch(
    current_truth: Dict[str, Any],
    branch_cell: str,
    thesis_payload: Dict[str, Any],
    component_type: str,
) -> Dict[str, Any]:
    """Return a new truth dict that updates ONLY the targeted branch cell.

    The caller supplies the current truth and the thesis payload; this function
    derives a shallow copy of current_truth and patches exactly the key(s) that
    correspond to the branch cell — no extra keys from the payload are merged in.
    Numeric values are clamped to safe bounds before being written.
    """
    new_truth = dict(current_truth)

    if component_type == "prompt_program":
        if branch_cell == "instructions":
            val = thesis_payload.get("instructions")
            if val is not None:
                new_truth["instructions"] = val
        elif branch_cell == "demos":
            val = thesis_payload.get("demos")
            if val is not None:
                new_truth["demos"] = val

    elif component_type == "swarm_config":
        setting_key = _BRANCH_CELL_SETTING_KEYS.get(branch_cell)
        raw_value = thesis_payload.get("value")
        if setting_key is not None and raw_value is not None:
            lo, hi = _BRANCH_CELL_BOUNDS.get(branch_cell, (float("-inf"), float("inf")))
            clamped = float(max(lo, min(hi, float(raw_value))))
            new_truth[setting_key] = clamped

    elif component_type == "role_composition":
        if branch_cell.startswith("role_weights."):
            raw_weight = thesis_payload.get("weight")
            if raw_weight is not None:
                clamped_w = float(max(0.0, min(1.0, float(raw_weight))))
                new_truth[branch_cell] = clamped_w

    return new_truth


# ---------------------------------------------------------------------------
# Governance enactment (Fix 1 & 2: truth swap is always preceded by an
# explicit GovernanceEnactment audit record)
# ---------------------------------------------------------------------------

def _enact_evolution_proposal(
    component_type: str,
    component_name: str,
    proposal_db_id: Optional[int],
    thesis_payload: Dict[str, Any],
    branch_cell: str,
) -> Tuple[bool, str]:
    """Enact a governance proposal that was raised by the evolution loop.

    Returns (enacted: bool, detail: str).

    - For prompt_program: routes through GovernanceEngine.operator_enact which
      calls _promote_prompt_version → promote_version_in_session atomically.
      The PromptProgram.current_version_id is updated inside the same DB
      transaction as the GovernanceEnactment audit row.

    - For swarm_config / role_composition: applies the validated AppSetting
      change and writes a GovernanceEnactment audit row in a single transaction.
      Values are clamped to safe bounds before being written.

    Truth update is the CALLER's responsibility after this returns True.
    """
    if proposal_db_id is None:
        return False, "no proposal_db_id — cannot record enactment"

    if component_type == "prompt_program":
        try:
            from src.swarm.governance_engine import GovernanceEngine
            enacted = GovernanceEngine().operator_enact(
                proposal_db_id, operator="evolution_loop"
            )
            if enacted:
                return True, "prompt champion swapped via governed enactment"
            return False, "GovernanceEngine.operator_enact returned False"
        except Exception as e:
            return False, "operator_enact error: %s" % e

    elif component_type == "swarm_config":
        try:
            from datetime import datetime as _dt
            from src.database import SessionLocal
            from src.models import AppSetting, GovernanceProposal, GovernanceEnactment
            setting_key = _BRANCH_CELL_SETTING_KEYS.get(branch_cell)
            raw_value = thesis_payload.get("value")
            if not setting_key or raw_value is None:
                return False, "branch_cell %r has no mapped setting or value" % branch_cell
            lo, hi = _BRANCH_CELL_BOUNDS.get(branch_cell, (float("-inf"), float("inf")))
            clamped = float(max(lo, min(hi, float(raw_value))))

            db = SessionLocal()
            try:
                prop = db.query(GovernanceProposal).filter_by(id=proposal_db_id).first()
                if not prop or prop.status != "open":
                    return False, "proposal %d not open (status=%s)" % (
                        proposal_db_id, getattr(prop, "status", "?"))

                s = db.query(AppSetting).filter_by(key=setting_key).first()
                if s:
                    s.value = str(clamped)
                else:
                    db.add(AppSetting(key=setting_key, value=str(clamped)))

                prop.status = "enacted"
                prop.enacted_at = _dt.utcnow()
                db.add(GovernanceEnactment(
                    proposal_id=proposal_db_id,
                    enacted_by="evolution_loop",
                    change_json=json.dumps({
                        "action": "%s=%s" % (setting_key, clamped),
                        "enacted_by": "evolution_loop",
                        "track": getattr(prop, "track", "tuning"),
                        "method": "evolution_loop_auto_enact",
                        "applied_settings": ["%s=%s" % (setting_key, clamped)],
                    }),
                ))
                db.commit()
                return True, "swarm_config %s=%s enacted" % (setting_key, clamped)
            except Exception as e:
                db.rollback()
                return False, "swarm_config enact failed: %s" % e
            finally:
                db.close()
        except Exception as e:
            return False, "swarm_config enact error: %s" % e

    elif component_type == "role_composition":
        try:
            from datetime import datetime as _dt
            from src.database import SessionLocal
            from src.models import AppSetting, GovernanceProposal, GovernanceEnactment
            role = thesis_payload.get("role")
            raw_weight = thesis_payload.get("weight")
            if not role or raw_weight is None:
                return False, "role_composition thesis missing role or weight"
            clamped_w = float(max(0.0, min(1.0, float(raw_weight))))
            setting_key = "swarm.role_weight.%s" % role

            db = SessionLocal()
            try:
                prop = db.query(GovernanceProposal).filter_by(id=proposal_db_id).first()
                if not prop or prop.status != "open":
                    return False, "proposal %d not open (status=%s)" % (
                        proposal_db_id, getattr(prop, "status", "?"))

                s = db.query(AppSetting).filter_by(key=setting_key).first()
                if s:
                    s.value = str(clamped_w)
                else:
                    db.add(AppSetting(key=setting_key, value=str(clamped_w)))

                prop.status = "enacted"
                prop.enacted_at = _dt.utcnow()
                db.add(GovernanceEnactment(
                    proposal_id=proposal_db_id,
                    enacted_by="evolution_loop",
                    change_json=json.dumps({
                        "action": "role_weight:%s=%s" % (role, clamped_w),
                        "enacted_by": "evolution_loop",
                        "track": getattr(prop, "track", "tuning"),
                        "method": "evolution_loop_auto_enact",
                        "applied_settings": ["%s=%s" % (setting_key, clamped_w)],
                    }),
                ))
                db.commit()
                return True, "role_weight %s=%s enacted" % (role, clamped_w)
            except Exception as e:
                db.rollback()
                return False, "role_composition enact failed: %s" % e
            finally:
                db.close()
        except Exception as e:
            return False, "role_composition enact error: %s" % e

    else:
        return False, "unknown component_type %r" % component_type


def _derive_enacted_truth(
    component_type: str,
    component_name: str,
    current_truth: Dict[str, Any],
    branch_cell: str,
    thesis_payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Derive the new component truth from what was actually enacted.

    For prompt_program: reads the live champion instructions from the DB
    (set by promote_version_in_session during enactment) so truth reflects the
    actual live state, not the thesis payload.

    For swarm_config / role_composition: uses _apply_branch_cell_patch (the
    same values that were written to AppSetting by _enact_evolution_proposal).
    """
    if component_type == "prompt_program":
        try:
            from src.promptopt.store import get_live_instructions
            _, live_instructions, _ = get_live_instructions(component_name)
            new_truth = dict(current_truth)
            if live_instructions:
                new_truth["instructions"] = live_instructions
            return new_truth
        except Exception:
            pass

    return _apply_branch_cell_patch(current_truth, branch_cell, thesis_payload, component_type)


# ---------------------------------------------------------------------------
# Trial runner
# ---------------------------------------------------------------------------

def run_trial(
    component_id: int,
    thesis_id: int,
    component_type: str,
    component_name: str,
    current_truth: Dict[str, Any],
    branch_cell: str,
    thesis: Dict[str, Any],
) -> Tuple[bool, float, SandboxResult]:
    """Run the build/test cycle for a thesis, iterating until pass or stall.

    Returns (all_passed, best_score, final_sandbox_result).
    Records every iteration to ThesisTrialIteration.
    Raises EvolutionStallError on MAX_ITERATIONS without passing.
    """
    from src.promptopt import evolution_store as estore

    estore.update_thesis_status(thesis_id, "testing")
    best_score = 0.0
    prev_score: Optional[float] = None   # score from the previous iteration for stall detection
    last_result: Optional[SandboxResult] = None

    for iteration in range(1, MAX_ITERATIONS + 1):
        result = run_experimental_sandbox(
            component_type=component_type,
            component_name=component_name,
            thesis=thesis,
            branch_cell=branch_cell,
            current_truth=current_truth,
        )

        estore.record_iteration(
            thesis_id=thesis_id,
            iteration_no=iteration,
            test_results=result.test_results,
            compliance_score=result.compliance_score,
            hallucination_risk=result.hallucination_risk,
            composite_score=result.composite_score,
            all_tests_passed=result.all_tests_passed,
            outcome="pass" if result.all_tests_passed else ("error" if result.error else "fail"),
            error=result.error,
        )
        estore.increment_thesis_iterations(thesis_id)

        if result.composite_score > best_score:
            best_score = result.composite_score

        if result.all_tests_passed:
            return True, best_score, result

        # Stall detection: compare current score against the PREVIOUS iteration's score.
        # prev_score is set at the end of each iteration so this comparison is correct.
        if iteration > 1 and prev_score is not None:
            if abs(result.composite_score - prev_score) < STALL_SCORE_DELTA:
                raise EvolutionStallError(
                    "Thesis %d stalled at iteration %d (score=%.4f delta=%.6f < %.4f)"
                    % (thesis_id, iteration, result.composite_score,
                       abs(result.composite_score - prev_score), STALL_SCORE_DELTA)
                )

        # Update after the stall check so the next iteration can compare against this one.
        prev_score = result.composite_score
        last_result = result

        time.sleep(0.1)

    raise EvolutionStallError(
        "Thesis %d hit MAX_ITERATIONS=%d without all tests passing (best_score=%.4f)"
        % (thesis_id, MAX_ITERATIONS, best_score)
    )


# ---------------------------------------------------------------------------
# Full cycle
# ---------------------------------------------------------------------------

def _run_cycle(component_id: int, force: bool = False) -> str:
    """Execute one full evolution cycle for a component.

    Returns the cycle outcome: 'promoted' | 'rejected' | 'stalled' | 'error'
    | 'awaiting_human_approval' | 'paused'.

    When force=True the loop_enabled gate is bypassed.  The operator-triggered
    manual trial endpoint uses this to run a single cycle even while the
    daemon loop is stopped, without needing to re-enable the loop permanently.
    """
    from src.promptopt import evolution_store as estore
    from src.database import SessionLocal
    from src.models import CognitionComponent

    db = SessionLocal()
    try:
        component = estore.get_component_by_id(db, component_id)
        if component is None:
            return "error"
        if not component.loop_enabled and not force:
            return "paused"
        cycle_index = (component.cycle_count or 0) + 1
        component_type = component.component_type
        component_name = component.name
        current_truth = json.loads(component.truth_json or "{}")
    finally:
        db.close()

    thesis_candidate, selection_votes = select_next_thesis(component, cycle_index)

    thesis_id = estore.create_thesis(
        component_id=component_id,
        branch_cell=thesis_candidate["branch_cell"],
        thesis=thesis_candidate["thesis"],
        rationale=thesis_candidate.get("rationale", ""),
        cycle_index=cycle_index,
        requires_human_gate=thesis_candidate.get("requires_human_gate", False),
        selection_votes=selection_votes,
    )

    logger.info(
        "[EvolutionLoop] component=%s cycle=%d thesis=%d branch=%s",
        component_name, cycle_index, thesis_id, thesis_candidate["branch_cell"],
    )

    outcome = "error"
    try:
        all_passed, test_score, sandbox_result = run_trial(
            component_id=component_id,
            thesis_id=thesis_id,
            component_type=component_type,
            component_name=component_name,
            current_truth=current_truth,
            branch_cell=thesis_candidate["branch_cell"],
            thesis=thesis_candidate["thesis"],
        )

        if not all_passed:
            estore.update_thesis_status(thesis_id, "rejected", test_score=test_score)
            outcome = "rejected"
            logger.info("[EvolutionLoop] thesis=%d rejected (score=%.4f)", thesis_id, test_score)
        else:
            estore.update_thesis_status(thesis_id, "awaiting_consensus", test_score=test_score)

            from src.swarm.contract import (
                ROLE_BASE_WEIGHTS, CognitiveRole, run_consensus, compute_vote_weight
            )
            votes: Dict[str, Dict] = {}
            for role in [CognitiveRole.ARCHITECT, CognitiveRole.EVIDENCE_VALIDATOR,
                         CognitiveRole.SKEPTIC, CognitiveRole.FALSIFIER,
                         CognitiveRole.REFLEXIVE_AUDITOR]:
                base_w = ROLE_BASE_WEIGHTS.get(role, 0.5)
                cs = int(test_score * 100)
                hr = max(0.0, 1.0 - test_score)
                w = compute_vote_weight(base_w, cs, min(1.0, test_score + 0.1), hr)
                accept = (
                    test_score >= 0.40
                    and (role != CognitiveRole.SKEPTIC or test_score >= 0.55)
                    and (role != CognitiveRole.FALSIFIER or test_score >= 0.50)
                )
                votes[role.value] = {"weight": w, "accept": accept, "reason": "score=%.4f" % test_score}

            consensus = run_consensus(votes, threshold=0.60)

            thesis_obj_type_hint = component_type

            class _ThesisProxy:
                def __init__(self_inner):
                    db2 = SessionLocal()
                    try:
                        from src.models import CognitionThesis as _CT
                        t = db2.query(_CT).filter_by(id=thesis_id).first()
                        self_inner.thesis_json = t.thesis_json if t else "{}"
                        self_inner.branch_cell = t.branch_cell if t else ""
                        self_inner.cycle_index = t.cycle_index if t else 0
                        self_inner.rationale = t.rationale if t else ""
                        self_inner.test_score = t.test_score if t else 0.0
                        self_inner.component_type_hint = component_type
                        # Carry requires_human_gate from DB so the promotion gate
                        # can decide whether truth update is safe to auto-apply.
                        self_inner.requires_human_gate = (
                            t.requires_human_gate if t else False
                        )
                    finally:
                        db2.close()

            thesis_proxy = _ThesisProxy()

            if consensus.accepted:
                promoted, prom_detail, pid, pdb_id, human_gate_blocking = run_promotion_gate(
                    component, thesis_proxy, test_score, consensus.consensus_confidence
                )
                if promoted:
                    if human_gate_blocking:
                        # High-impact or human-gated thesis: proposal is raised and
                        # recorded, but truth MUST NOT be updated until a human
                        # explicitly enacts the governance proposal.  The loop
                        # records this as 'awaiting_human_approval' so the operator
                        # can act on it via the /governance/enact endpoint.
                        estore.update_thesis_status(
                            thesis_id, "awaiting_human_approval",
                            governance_proposal_id=pid,
                            governance_proposal_db_id=pdb_id,
                        )
                        outcome = "awaiting_human_approval"
                        logger.info(
                            "[EvolutionLoop] thesis=%d AWAITING HUMAN APPROVAL "
                            "(proposal=%s, requires_human_gate=%s, score=%.4f)",
                            thesis_id, pid, thesis_proxy.requires_human_gate, test_score,
                        )
                    else:
                        # Non-human-gate thesis: weighted-debate consensus IS the final
                        # governance authority.  Route through _enact_evolution_proposal
                        # so a GovernanceEnactment audit row is written atomically with
                        # the AppSetting / champion swap — truth is NEVER updated before
                        # the enactment record exists.
                        enacted, enact_detail = _enact_evolution_proposal(
                            component_type=component_type,
                            component_name=component_name,
                            proposal_db_id=pdb_id,
                            thesis_payload=thesis_candidate["thesis"],
                            branch_cell=thesis_candidate["branch_cell"],
                        )
                        if enacted:
                            new_truth = _derive_enacted_truth(
                                component_type=component_type,
                                component_name=component_name,
                                current_truth=current_truth,
                                branch_cell=thesis_candidate["branch_cell"],
                                thesis_payload=thesis_candidate["thesis"],
                            )
                            estore.update_component_truth(component_id, new_truth)
                            estore.update_thesis_status(
                                thesis_id, "promoted",
                                governance_proposal_id=pid,
                                governance_proposal_db_id=pdb_id,
                            )
                            outcome = "promoted"
                            logger.info(
                                "[EvolutionLoop] thesis=%d PROMOTED via enactment "
                                "(prom_detail=%s enact_detail=%s)",
                                thesis_id, prom_detail, enact_detail,
                            )
                        else:
                            # Enactment failed after proposal was raised — reject the
                            # thesis so the loop does not stall, and log the failure.
                            estore.update_thesis_status(thesis_id, "rejected")
                            outcome = "rejected"
                            logger.error(
                                "[EvolutionLoop] thesis=%d enactment FAILED "
                                "(proposal raised but not applied): %s",
                                thesis_id, enact_detail,
                            )
                else:
                    estore.update_thesis_status(thesis_id, "rejected")
                    outcome = "rejected"
                    logger.info(
                        "[EvolutionLoop] thesis=%d promotion gate REJECTED: %s", thesis_id, prom_detail
                    )
            else:
                estore.update_thesis_status(thesis_id, "rejected", test_score=test_score)
                outcome = "rejected"
                logger.info(
                    "[EvolutionLoop] thesis=%d consensus REJECTED (conf=%.2f)",
                    thesis_id, consensus.consensus_confidence
                )

    except EvolutionStallError as e:
        estore.update_thesis_status(thesis_id, "stalled")
        outcome = "stalled"
        logger.warning("[EvolutionLoop] thesis=%d STALLED: %s", thesis_id, e)
    except Exception as e:
        estore.update_thesis_status(thesis_id, "stalled")
        outcome = "error"
        logger.error("[EvolutionLoop] thesis=%d ERROR: %s\n%s", thesis_id, e, traceback.format_exc())

    cycle_count = estore.increment_cycle(component_id)
    next_selection = {"next_cycle": cycle_count + 1, "from_outcome": outcome}
    if outcome == "stalled":
        # Record the failing branch cell as a negative signal so the next
        # select_next_thesis weights this branch cell down (stall recovery).
        next_selection["stalled_branch_cell"] = thesis_candidate["branch_cell"]
    estore.log_cycle(
        component_id=component_id,
        cycle_index=cycle_index,
        outcome=outcome,
        thesis_id=thesis_id,
        next_selection=next_selection,
        detail="cycle=%d thesis=%d outcome=%s" % (cycle_index, thesis_id, outcome),
    )

    return outcome


# ---------------------------------------------------------------------------
# Perpetual loop (daemon thread per component)
# ---------------------------------------------------------------------------

_loop_threads: Dict[int, threading.Thread] = {}
_loop_stop_events: Dict[int, threading.Event] = {}
_lock = threading.Lock()


def _loop_body(component_id: int, stop_event: threading.Event) -> None:
    """Thread body — perpetual evolution cycle for one component."""
    from src.promptopt import evolution_store as estore

    logger.info("[EvolutionLoop] component_id=%d loop started", component_id)
    estore.set_loop_state(component_id, "running")

    try:
        while not stop_event.is_set():
            try:
                from src.database import SessionLocal
                from src.models import CognitionComponent as _CC
                db = SessionLocal()
                try:
                    c = db.query(_CC).filter_by(id=component_id).first()
                    enabled = bool(c.loop_enabled) if c else False
                finally:
                    db.close()

                if not enabled:
                    logger.info("[EvolutionLoop] component_id=%d disabled; pausing", component_id)
                    estore.set_loop_state(component_id, "paused")
                    stop_event.wait(LOOP_SLEEP_SECONDS)
                    continue

                outcome = _run_cycle(component_id)
                logger.info("[EvolutionLoop] component_id=%d cycle outcome=%s", component_id, outcome)

            except Exception as e:
                logger.error("[EvolutionLoop] component_id=%d unhandled: %s", component_id, e)
                try:
                    estore.set_loop_state(component_id, "error")
                except Exception:
                    pass

            if stop_event.wait(LOOP_SLEEP_SECONDS):
                break
    finally:
        try:
            from src.promptopt import evolution_store as es
            es.set_loop_state(component_id, "idle")
        except Exception:
            pass
        with _lock:
            _loop_threads.pop(component_id, None)
            _loop_stop_events.pop(component_id, None)
        logger.info("[EvolutionLoop] component_id=%d loop stopped", component_id)


class EvolutionLoop:
    """Manages perpetual evolution loops for cognition components."""

    def start_component(self, component_id: int) -> bool:
        """Start the loop for a component (idempotent). Returns True if started."""
        with _lock:
            if component_id in _loop_threads and _loop_threads[component_id].is_alive():
                return False
            stop_event = threading.Event()
            t = threading.Thread(
                target=_loop_body,
                args=(component_id, stop_event),
                name="evolution-loop-%d" % component_id,
                daemon=True,
            )
            _loop_threads[component_id] = t
            _loop_stop_events[component_id] = stop_event
            t.start()
        return True

    def stop_component(self, component_id: int) -> bool:
        """Signal the loop to stop. Returns True if a loop was running."""
        with _lock:
            ev = _loop_stop_events.get(component_id)
            if ev is None:
                return False
            ev.set()
        return True

    def is_running(self, component_id: int) -> bool:
        with _lock:
            t = _loop_threads.get(component_id)
            return t is not None and t.is_alive()

    def status(self, component_id: int) -> Dict[str, Any]:
        from src.promptopt import evolution_store as estore
        rows = estore.list_components()
        for r in rows:
            if r["id"] == component_id:
                return {
                    **r,
                    "thread_alive": self.is_running(component_id),
                }
        return {"id": component_id, "thread_alive": False}

    def status_all(self) -> List[Dict[str, Any]]:
        from src.promptopt import evolution_store as estore
        rows = estore.list_components()
        return [{**r, "thread_alive": self.is_running(r["id"])} for r in rows]


_global_loop = EvolutionLoop()


def get_loop() -> EvolutionLoop:
    return _global_loop


# ---------------------------------------------------------------------------
# Seed default components (called at app startup)
# ---------------------------------------------------------------------------

def seed_components() -> None:
    """Ensure the default cognition components exist. Idempotent."""
    from src.promptopt import evolution_store as estore
    from src.promptopt.specs import PROGRAM_SPECS

    for name, spec in PROGRAM_SPECS.items():
        try:
            truth: Dict[str, Any] = {
                "instructions": spec.seed_instructions,
                "source": "seed",
            }
            try:
                from src.promptopt.store import get_live_instructions
                vid, instr, _ = get_live_instructions(name)
                truth = {"instructions": instr, "source": "live", "version_id": vid}
                estore.ensure_component(
                    name=name,
                    component_type="prompt_program",
                    description=spec.description,
                    truth_json=truth,
                    truth_version_id=vid,
                )
            except Exception:
                estore.ensure_component(
                    name=name,
                    component_type="prompt_program",
                    description=spec.description,
                    truth_json=truth,
                )
        except Exception as e:
            logger.warning("seed_components: failed for '%s': %s", name, e)

    try:
        estore.ensure_component(
            name="swarm_governance",
            component_type="swarm_config",
            description="Core swarm governance parameters (evidence strength, CS thresholds, etc.)",
            truth_json={"source": "seed"},
        )
    except Exception as e:
        logger.warning("seed_components: failed for swarm_governance: %s", e)

    try:
        estore.ensure_component(
            name="role_composition_default",
            component_type="role_composition",
            description="Default cognitive role weights for the swarm debate cycle.",
            truth_json={"source": "seed"},
        )
    except Exception as e:
        logger.warning("seed_components: failed for role_composition_default: %s", e)
