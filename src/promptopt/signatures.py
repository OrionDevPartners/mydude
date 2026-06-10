"""DSPy Signatures for the optimizable prompt programs.

Imports dspy, so this module is only loaded on the runtime/optimization paths
(never at app startup). The signature *docstring* is only the default; the LIVE,
governance-approved instruction text is loaded from the DB and applied at runtime
via :func:`build_signature`.
"""
from typing import Dict, Type

import dspy

from src.promptopt.specs import JUDGE_PROGRAM, SEED_JUDGE_INSTRUCTIONS, get_spec


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


_SIGNATURES: Dict[str, Type[dspy.Signature]] = {
    JUDGE_PROGRAM: JudgeSynthesis,
}

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
