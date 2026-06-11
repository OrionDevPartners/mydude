"""Lightweight, dspy-free metadata for the prompt programs.

Kept import-cheap so the app startup path (seed + orphan recovery) can run
without importing dspy. The actual ``dspy.Signature`` classes live in
``signatures.py`` and reference these specs.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

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


# ---------------------------------------------------------------------------
# Cognitive-role programs (the swarm's debate-cycle role prompts).
#
# The swarm runs per-agent cognitive roles whose guidance was previously a
# hardcoded prompt string. Each role below is registered as its OWN optimizable
# program — same governance gate (versioned, approve-to-promote, audited
# rollback) and same MIPROv2/GEPA flow as the judge. Every role agent emits the
# worker output format the orchestrator already parses, so the metric's
# format-adherence term reuses the six worker sections.
# ---------------------------------------------------------------------------

# Shared signature + IO contract for every role program (one DSPy signature,
# instructions differ per program — exactly like the judge).
ROLE_SIGNATURE = "RoleAgent"
ROLE_INPUT_FIELDS: List[str] = ["goal", "task", "context", "mode"]
ROLE_OUTPUT_FIELD = "worker_output"

# Appended to each role's seed so the governed output stays parseable by the
# orchestrator's _parse_worker and scorable by the same format metric.
ROLE_WORKER_OUTPUT_CONTRACT = (
    "\n\nOUTPUT CONTRACT (MANDATORY):\n"
    "Apply your cognitive-role discipline to the scoped TASK, then return the result "
    "in worker format. Include EVERY one of these section headers exactly: RESULT, "
    "ARTIFACTS, CHECKS, RISKS, CAPABILITIES, COMPRESSED_HANDOFF.\n"
    "- RESULT: your role's primary finding for this task (concise; reference evidence).\n"
    "- ARTIFACTS: concrete outputs — diffs, commands, or per-claim verdicts — or 'none'.\n"
    "- CHECKS: tests, validations, or audits you ran or propose.\n"
    "- RISKS: hazards, unverified claims, and unknowns (label VERIFIED/DERIVED/HYPOTHESIS/UNKNOWN).\n"
    "- CAPABILITIES: broker capability requests (name + JSON params), or 'none'.\n"
    "- COMPRESSED_HANDOFF: a compact JSON handoff with goal, facts, decisions, risks, next.\n"
    "Request broker capabilities rather than handling secrets. Never emit raw secrets."
)


def _role_program_name(cognitive_role_value: str) -> str:
    return "role_" + cognitive_role_value


# cognitive-role value -> (prompts.py constant name, short description). Kept as a
# table so adding another governed role is a single line. The seed text is the
# existing hardcoded role prompt + the worker output contract (no divergent copy).
_ROLE_TABLE: Dict[str, Tuple[str, str]] = {
    "architect": (
        "ARCHITECT_PROMPT",
        "Architect role: propose solution structure and the initial evidence-backed "
        "claim ledger for the wave.",
    ),
    "skeptic": (
        "SKEPTIC_PROMPT",
        "Skeptic role: adversarially audit assumptions and push downgrades on "
        "unverified claims.",
    ),
    "evidence_validator": (
        "EVIDENCE_VALIDATOR_PROMPT",
        "Evidence Validator role: verify pointers and citations, score evidence "
        "strength, reject unsupported claims.",
    ),
    "falsifier": (
        "FALSIFIER_PROMPT",
        "Falsifier role: seek counterexamples and logical flaws; surviving claims "
        "are strengthened.",
    ),
}


def _build_role_specs() -> Dict[str, ProgramSpec]:
    # Import here (not at module top) only to read the existing prompt strings.
    # prompts.py is dspy-free and import-cheap, so app startup stays light.
    from src.swarm import prompts as _prompts
    out: Dict[str, ProgramSpec] = {}
    for cog_value, (const_name, description) in _ROLE_TABLE.items():
        role_prompt = getattr(_prompts, const_name, "").strip()
        if not role_prompt:
            continue
        name = _role_program_name(cog_value)
        out[name] = ProgramSpec(
            name=name,
            signature_name=ROLE_SIGNATURE,
            description=description,
            seed_instructions=role_prompt + ROLE_WORKER_OUTPUT_CONTRACT,
            input_fields=list(ROLE_INPUT_FIELDS),
            output_field=ROLE_OUTPUT_FIELD,
            required_sections=list(REQUIRED_SECTIONS),
        )
    return out


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
PROGRAM_SPECS.update(_build_role_specs())

# Program names of the governed cognitive-role agents (signatures.py maps each to
# the shared RoleAgent signature).
ROLE_PROGRAM_NAMES: List[str] = [
    _role_program_name(v) for v in _ROLE_TABLE if _role_program_name(v) in PROGRAM_SPECS
]


def role_program_for(cognitive_role_value: str) -> Optional[str]:
    """Return the governed program name for a cognitive role, or None if that role
    is not (yet) registered as an optimizable program."""
    if not cognitive_role_value:
        return None
    name = _role_program_name(cognitive_role_value)
    return name if name in PROGRAM_SPECS else None


def get_spec(name: str) -> ProgramSpec:
    spec = PROGRAM_SPECS.get(name)
    if spec is None:
        raise KeyError(
            "Unknown prompt program '%s'. Known: %s"
            % (name, ", ".join(sorted(PROGRAM_SPECS)))
        )
    return spec
