"""DSPy Signatures for the optimizable prompt programs.

Imports dspy, so this module is only loaded on the runtime/optimization paths
(never at app startup). The signature *docstring* is only the default; the LIVE,
governance-approved instruction text is loaded from the DB and applied at runtime
via :func:`build_signature`.
"""
from typing import Dict, Type

import dspy

from src.promptopt.specs import (
    JUDGE_PROGRAM,
    ROLE_PROGRAM_NAMES,
    SEED_JUDGE_INSTRUCTIONS,
    SYNTHESIZER_PROGRAM,
    RED_TEAM_PROGRAM,
    REFLEXIVE_AUDITOR_PROGRAM,
    get_spec,
)


class JudgeSynthesis(dspy.Signature):
    """Synthesize the providers' outputs into one consolidated worker-format answer."""

    user_request = dspy.InputField(
        desc="The original user request the swarm is answering."
    )
    provider_outputs = dspy.InputField(
        desc="Each provider's output, annotated with its compliance score (CS) "
             "and hallucination risk (HR)."
    )
    risk_directive = dspy.InputField(
        desc="An optional risk-control directive to honor; may be empty."
    )
    consolidated_response = dspy.OutputField(
        desc="The single consolidated worker-format answer. MUST include every "
             "section header exactly: RESULT, ARTIFACTS, CHECKS, RISKS, "
             "CAPABILITIES, COMPRESSED_HANDOFF."
    )


class RoleAgent(dspy.Signature):
    """A governed cognitive-role agent producing one worker-format answer.

    One shared signature backs every role program; the role's discipline lives in
    the LIVE, governance-approved instructions loaded per call (not this docstring).
    """

    goal = dspy.InputField(desc="The overall swarm goal under deliberation.")
    task = dspy.InputField(
        desc="The scoped task assigned to this agent in this wave."
    )
    context = dspy.InputField(
        desc="Prior facts, decisions, active constraints, and handoff context; "
             "may be empty."
    )
    mode = dspy.InputField(desc="Reasoning mode: ANALYTIC or EXPLORATORY.")
    worker_output = dspy.OutputField(
        desc="The worker-format answer. MUST include every section header exactly: "
             "RESULT, ARTIFACTS, CHECKS, RISKS, CAPABILITIES, COMPRESSED_HANDOFF."
    )


class SynthesizerAgent(dspy.Signature):
    """Merge the wave's accepted claims into one governed final synthesis.

    A thinking-role program (NOT worker format). Synthesizes ONLY from the
    accepted inputs — it introduces no new claims. The live, governance-approved
    instructions are loaded per call (not this docstring)."""

    goal = dspy.InputField(desc="The overall swarm goal under deliberation.")
    facts = dspy.InputField(desc="Accepted key findings/facts from the debate.")
    decisions = dspy.InputField(desc="Decisions reached from accepted claims.")
    tasks = dspy.InputField(desc="Outstanding next tasks/actions.")
    risks = dspy.InputField(desc="Residual risks and unknowns.")
    claim_ledger = dspy.InputField(desc="The claim-ledger summary; may be empty.")
    risk_directive = dspy.InputField(desc="Optional risk-control directive; may be empty.")
    synthesis = dspy.OutputField(
        desc="The governed final handoff. MUST include every section header exactly: "
             "GOAL, FINDINGS, DECISIONS, NEXT_STEPS, RISKS."
    )


class RedTeamReview(dspy.Signature):
    """Adversarially test the swarm's synthesis for exploitable weaknesses.

    A thinking-role program (NOT worker format). Reports findings + fixes per
    attack vector; never emits a working exploit. Live instructions are loaded
    per call (not this docstring)."""

    synthesis = dspy.InputField(desc="The swarm's consolidated synthesis to attack.")
    claim_ledger = dspy.InputField(desc="The claim-ledger summary; may be empty.")
    context = dspy.InputField(desc="Supporting facts/context for the probes; may be empty.")
    red_team_report = dspy.OutputField(
        desc="One verdict per attack vector. MUST include every section header exactly: "
             "PROMPT_INJECTION, EVIDENCE_FABRICATION, CONSTRAINT_BYPASS, LABEL_CONFUSION, "
             "BOUNDARY_VIOLATION."
    )


class ReflexiveAuditorReview(dspy.Signature):
    """Meta-cognitive audit of the swarm's OWN process/governance health.

    A thinking-role program (NOT worker format). Judges drift/anomalies/consensus
    health from the swarm's performance summary, never re-judging the task's
    subject matter. Live instructions are loaded per call (not this docstring)."""

    ledger_summary = dspy.InputField(desc="The reflexive auditor's performance-ledger summary.")
    trend_summary = dspy.InputField(desc="CS/HR/consensus trend labels for the run.")
    wave_stats = dspy.InputField(desc="Per-run stats: waves recorded, anomalies, avg HR, abort state.")
    existing_meta_claims = dspy.InputField(desc="The heuristic meta-claims already raised; may be 'none'.")
    audit_report = dspy.OutputField(
        desc="The meta-analysis. MUST include every section header exactly: META_CLAIMS, "
             "CATEGORY, SEVERITY, DESCRIPTION, EVIDENCE, PROPOSED_ACTION."
    )


_SIGNATURES: Dict[str, Type[dspy.Signature]] = {
    JUDGE_PROGRAM: JudgeSynthesis,
}
# Every governed cognitive-role program shares the RoleAgent signature; their
# instructions (the role discipline) differ per program and are governed in the DB.
for _role_name in ROLE_PROGRAM_NAMES:
    _SIGNATURES[_role_name] = RoleAgent

# The three thinking-role programs each use their OWN signature (distinct IO).
_THINKING_ROLE_SIGNATURES: Dict[str, Type[dspy.Signature]] = {
    SYNTHESIZER_PROGRAM: SynthesizerAgent,
    RED_TEAM_PROGRAM: RedTeamReview,
    REFLEXIVE_AUDITOR_PROGRAM: ReflexiveAuditorReview,
}
for _name, _sig in _THINKING_ROLE_SIGNATURES.items():
    try:
        _spec = get_spec(_name)
    except KeyError:
        continue  # prompt seed absent -> program not registered; skip its signature
    _SIGNATURES[_name] = _sig
    # Seed the default docstring to the v1 instructions so a fresh (uncompiled)
    # signature behaves identically to the seeded live version (judge parity).
    _sig.__doc__ = _spec.seed_instructions

# Seed the default docstring to the same text used for DB version 1, so a fresh
# (uncompiled) signature behaves identically to the seeded live version.
JudgeSynthesis.__doc__ = SEED_JUDGE_INSTRUCTIONS


def base_signature(program_name: str) -> Type[dspy.Signature]:
    sig = _SIGNATURES.get(program_name)
    if sig is None:
        raise KeyError("No dspy.Signature registered for program '%s'" % program_name)
    return sig


def build_signature(program_name: str, instructions: str) -> Type[dspy.Signature]:
    """Return a signature class carrying the given (live/candidate) instructions."""
    sig = base_signature(program_name)
    text = (instructions or "").strip() or get_spec(program_name).seed_instructions
    return sig.with_instructions(text)
