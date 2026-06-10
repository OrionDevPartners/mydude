"""Lightweight, dspy-free metadata for the prompt programs.

Kept import-cheap so the app startup path (seed + orphan recovery) can run
without importing dspy. The actual ``dspy.Signature`` classes live in
``signatures.py`` and reference these specs.
"""
from dataclasses import dataclass, field
from typing import Dict, List

# The merger/judge synthesis program — the reference behavior migrated off a
# hardcoded string (was MultiProviderLLM._judge_merge's inline judge_prompt).
JUDGE_PROGRAM = "judge_synthesis"

# The single output field the judge produces (parsed back out of the DSPy run).
JUDGE_OUTPUT_FIELD = "consolidated_response"

# The six worker-format sections the consolidated answer MUST contain. The
# metric's format-adherence term is the fraction of these present (hard check).
REQUIRED_SECTIONS: List[str] = [
    "RESULT", "ARTIFACTS", "CHECKS", "RISKS", "CAPABILITIES", "COMPRESSED_HANDOFF",
]

# Seed instruction text — extracted verbatim (static parts) from the current
# hardcoded judge_prompt so version 1 reproduces today's behavior exactly. The
# dynamic user request / provider outputs / risk directive become input fields.
SEED_JUDGE_INSTRUCTIONS = (
    "You are the MERGER/JUDGE.\n\n"
    "Goal:\n"
    "Synthesize the providers' outputs into one best answer with:\n"
    "- high correctness\n"
    "- strong security posture\n"
    "- concrete, actionable steps\n"
    "- minimal token bloat\n\n"
    "Each provider's output includes a compliance score (CS) and a hallucination "
    "risk (HR). WEIGHT providers by compliance score: higher CS = more trustworthy. "
    "Reject claims from providers with CS < 65.\n"
    "NOVEL HYPOTHESES: Do NOT reject novel ideas or creative theories just because "
    "they lack traditional evidence. If 3+ providers converge on a novel concept, "
    "treat it as HIGH CONFIDENCE. Innovation lives in the edges.\n"
    "Honor the risk directive when one is provided.\n"
    "Return ONLY the final consolidated worker-format answer, and include EVERY one "
    "of these section headers exactly: RESULT, ARTIFACTS, CHECKS, RISKS, "
    "CAPABILITIES, COMPRESSED_HANDOFF."
)


@dataclass(frozen=True)
class ProgramSpec:
    """Static description of an optimizable program (no dspy dependency)."""
    name: str
    signature_name: str
    description: str
    seed_instructions: str
    input_fields: List[str] = field(default_factory=list)
    output_field: str = ""
    required_sections: List[str] = field(default_factory=list)


PROGRAM_SPECS: Dict[str, ProgramSpec] = {
    JUDGE_PROGRAM: ProgramSpec(
        name=JUDGE_PROGRAM,
        signature_name="JudgeSynthesis",
        description=(
            "Merger/Judge: synthesize the multi-provider swarm outputs into one "
            "consolidated, governance-scored worker-format answer."
        ),
        seed_instructions=SEED_JUDGE_INSTRUCTIONS,
        input_fields=["user_request", "provider_outputs", "risk_directive"],
        output_field=JUDGE_OUTPUT_FIELD,
        required_sections=list(REQUIRED_SECTIONS),
    ),
}


def get_spec(name: str) -> ProgramSpec:
    spec = PROGRAM_SPECS.get(name)
    if spec is None:
        raise KeyError(
            "Unknown prompt program '%s'. Known: %s"
            % (name, ", ".join(sorted(PROGRAM_SPECS)))
        )
    return spec
