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


_SIGNATURES: Dict[str, Type[dspy.Signature]] = {
    JUDGE_PROGRAM: JudgeSynthesis,
}
# Every governed cognitive-role program shares the RoleAgent signature; their
# instructions (the role discipline) differ per program and are governed in the DB.
for _role_name in ROLE_PROGRAM_NAMES:
    _SIGNATURES[_role_name] = RoleAgent

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
