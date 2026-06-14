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


# ---------------------------------------------------------------------------
# Thinking-role programs (synthesizer, red-team, reflexive auditor).
#
# Unlike the worker-format cognitive roles above, these three "thinking" roles
# do NOT emit the six worker sections — each produces a DISTINCT structured
# output, so each gets its OWN dspy.Signature (distinct input fields) and its OWN
# required-section contract. They are registered as governed, optimizable
# programs exactly like the judge/roles (same MIPROv2/GEPA flow, same
# approve-to-promote / audited-rollback gate, same dashboard) and their live runs
# capture PromptTraces that feed that optimizer. Seed text = the previously
# dormant role prompt (prompts.py) + an explicit output contract so version 1 is
# fully operative (no placeholder).
# ---------------------------------------------------------------------------

SYNTHESIZER_PROGRAM = "role_synthesizer"
SYNTHESIZER_OUTPUT_FIELD = "synthesis"
SYNTHESIZER_INPUT_FIELDS: List[str] = [
    "goal", "facts", "decisions", "tasks", "risks", "claim_ledger", "risk_directive",
]
SYNTHESIZER_SECTIONS: List[str] = ["GOAL", "FINDINGS", "DECISIONS", "NEXT_STEPS", "RISKS"]
SYNTHESIZER_OUTPUT_CONTRACT = (
    "\n\nOUTPUT CONTRACT (MANDATORY):\n"
    "Synthesize ONLY from the accepted facts, decisions, tasks, risks, claim ledger, "
    "and risk directive provided as input — introduce NO new claims, evidence, or "
    "recommendations of your own. Return the final governed handoff with EVERY one of "
    "these section headers exactly: GOAL, FINDINGS, DECISIONS, NEXT_STEPS, RISKS.\n"
    "- GOAL: restate the goal under deliberation.\n"
    "- FINDINGS: the accepted key findings (one bullet per fact; cite claim ids when present).\n"
    "- DECISIONS: the decisions reached from accepted claims only.\n"
    "- NEXT_STEPS: the next tasks/actions to take.\n"
    "- RISKS: residual risks and unknowns (label VERIFIED/DERIVED/HYPOTHESIS/UNKNOWN).\n"
    "If the accepted inputs are insufficient to synthesize, say so under FINDINGS rather "
    "than inventing content."
)

RED_TEAM_PROGRAM = "role_red_team"
RED_TEAM_OUTPUT_FIELD = "red_team_report"
RED_TEAM_INPUT_FIELDS: List[str] = ["synthesis", "claim_ledger", "context"]
RED_TEAM_SECTIONS: List[str] = [
    "PROMPT_INJECTION", "EVIDENCE_FABRICATION", "CONSTRAINT_BYPASS",
    "LABEL_CONFUSION", "BOUNDARY_VIOLATION",
]
RED_TEAM_OUTPUT_CONTRACT = (
    "\n\nOUTPUT CONTRACT (MANDATORY):\n"
    "Adversarially test the provided synthesis and claim ledger against each attack "
    "vector. Return EXACTLY one entry per section header below, each header used "
    "verbatim: PROMPT_INJECTION, EVIDENCE_FABRICATION, CONSTRAINT_BYPASS, "
    "LABEL_CONFUSION, BOUNDARY_VIOLATION.\n"
    "- For each vector emit either 'VULNERABILITY: <what fails> | FIX: <required_fix>' "
    "or 'CLEAR: <one-line reason it holds>'.\n"
    "Describe weaknesses precisely but NEVER include a working exploit, payload, or "
    "step-by-step attack — only the finding and the fix."
)

REFLEXIVE_AUDITOR_PROGRAM = "role_reflexive_auditor"
REFLEXIVE_AUDITOR_OUTPUT_FIELD = "audit_report"
REFLEXIVE_AUDITOR_INPUT_FIELDS: List[str] = [
    "ledger_summary", "trend_summary", "wave_stats", "existing_meta_claims",
]
REFLEXIVE_AUDITOR_SECTIONS: List[str] = [
    "META_CLAIMS", "CATEGORY", "SEVERITY", "DESCRIPTION", "EVIDENCE", "PROPOSED_ACTION",
]
REFLEXIVE_AUDITOR_OUTPUT_CONTRACT = (
    "\n\nOUTPUT CONTRACT (MANDATORY):\n"
    "Perform a meta-cognitive audit of the swarm's OWN process health from the ledger "
    "summary, trend summary, wave stats, and existing heuristic meta-claims provided as "
    "input. Judge governance/process quality (drift, anomalies, consensus health) — do "
    "NOT re-judge the task's subject matter. Return your analysis with EVERY one of these "
    "section headers exactly: META_CLAIMS, CATEGORY, SEVERITY, DESCRIPTION, EVIDENCE, "
    "PROPOSED_ACTION.\n"
    "- META_CLAIMS: count + one-line overview of the meta-claims you assert.\n"
    "- CATEGORY: drift | performance | anomaly | recommendation.\n"
    "- SEVERITY: info | warning | critical.\n"
    "- DESCRIPTION: what you observed about the process.\n"
    "- EVIDENCE: specific data points (wave indices, CS/HR values, trend labels) — cite them.\n"
    "- PROPOSED_ACTION: phrase as a governance proposal for operator review; never a silent "
    "parameter change.\n"
    "If the data is insufficient, say so under DESCRIPTION rather than fabricating trends."
)

# program name -> (prompts.py constant, signature name, description, input fields,
# output field, required sections, output contract). One row per thinking role.
_THINKING_ROLE_TABLE: Dict[str, Tuple[str, str, str, List[str], str, List[str], str]] = {
    SYNTHESIZER_PROGRAM: (
        "SYNTHESIZER_GUARD",
        "SynthesizerAgent",
        "Synthesizer: merge the wave's accepted claims into one governed final handoff "
        "(accepted-claims-only; introduces no new claims).",
        SYNTHESIZER_INPUT_FIELDS,
        SYNTHESIZER_OUTPUT_FIELD,
        SYNTHESIZER_SECTIONS,
        SYNTHESIZER_OUTPUT_CONTRACT,
    ),
    RED_TEAM_PROGRAM: (
        "RED_TEAM_PROMPT",
        "RedTeamReview",
        "Red Team: adversarially probe the swarm's synthesis for prompt-injection, "
        "evidence-fabrication, constraint-bypass, label-confusion, and boundary weaknesses.",
        RED_TEAM_INPUT_FIELDS,
        RED_TEAM_OUTPUT_FIELD,
        RED_TEAM_SECTIONS,
        RED_TEAM_OUTPUT_CONTRACT,
    ),
    REFLEXIVE_AUDITOR_PROGRAM: (
        "REFLEXIVE_AUDITOR_PROMPT",
        "ReflexiveAuditorReview",
        "Reflexive Auditor: meta-cognitive audit of the swarm's own process health "
        "(drift, anomalies, consensus) surfaced as reviewable governance proposals.",
        REFLEXIVE_AUDITOR_INPUT_FIELDS,
        REFLEXIVE_AUDITOR_OUTPUT_FIELD,
        REFLEXIVE_AUDITOR_SECTIONS,
        REFLEXIVE_AUDITOR_OUTPUT_CONTRACT,
    ),
}


def _build_thinking_role_specs() -> Dict[str, ProgramSpec]:
    from src.swarm import prompts as _prompts
    out: Dict[str, ProgramSpec] = {}
    for name, (const_name, sig_name, desc, in_fields, out_field, sections, contract) in (
        _THINKING_ROLE_TABLE.items()
    ):
        base = getattr(_prompts, const_name, "").strip()
        if not base:
            continue
        out[name] = ProgramSpec(
            name=name,
            signature_name=sig_name,
            description=desc,
            seed_instructions=base + contract,
            input_fields=list(in_fields),
            output_field=out_field,
            required_sections=list(sections),
        )
    return out


PROGRAM_SPECS.update(_build_thinking_role_specs())

# Program names of the governed thinking-role agents (each maps to its OWN
# signature in signatures.py).
THINKING_ROLE_PROGRAM_NAMES: List[str] = [
    n for n in _THINKING_ROLE_TABLE if n in PROGRAM_SPECS
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
