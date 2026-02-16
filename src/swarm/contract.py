from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List


class CognitiveRole(Enum):
    ARCHITECT = "architect"
    SKEPTIC = "skeptic"
    EVIDENCE_VALIDATOR = "evidence_validator"
    CONSTRAINT_ENFORCER = "constraint_enforcer"
    CREATIVE_DIVERGENCE = "creative_divergence"
    SYNTHESIZER = "synthesizer"
    REFLEXIVE_AUDITOR = "reflexive_auditor"
    RED_TEAM = "red_team"
    FALSIFIER = "falsifier"


class DebateRound(Enum):
    GENESIS_BINDING = 0
    PROPOSAL = 1
    ADVERSARIAL_AUDIT = 2
    EVIDENCE_VALIDATION = 3
    CREATIVE_DIVERGENCE = 4
    CONSENSUS = 5
    SYNTHESIS = 6
    ADVERSARIAL_TESTING = 7


@dataclass
class AgentMessage:
    agent_id: str
    role: CognitiveRole
    mode: str
    intent_refs: List[str]
    claims: List[Dict]
    requests: List[Dict]
    compliance_score: int
    hallucination_risk: float


@dataclass
class DissentRecord:
    claim_id: str
    dissenters: List[str]
    reason: str
    risk_level: str


@dataclass
class ConsensusResult:
    claim_id: str
    accepted: bool
    consensus_confidence: float
    weighted_votes: Dict[str, float]
    dissent: List[DissentRecord] = field(default_factory=list)


ROLE_BASE_WEIGHTS: Dict[CognitiveRole, float] = {
    CognitiveRole.ARCHITECT: 1.0,
    CognitiveRole.SKEPTIC: 0.9,
    CognitiveRole.EVIDENCE_VALIDATOR: 1.0,
    CognitiveRole.CONSTRAINT_ENFORCER: 0.8,
    CognitiveRole.CREATIVE_DIVERGENCE: 0.6,
    CognitiveRole.SYNTHESIZER: 0.0,
    CognitiveRole.REFLEXIVE_AUDITOR: 0.7,
    CognitiveRole.RED_TEAM: 0.5,
    CognitiveRole.FALSIFIER: 0.85,
}


def compute_vote_weight(
    base_role_weight: float,
    compliance_score: int,
    evidence_strength: float,
    hallucination_risk: float,
) -> float:
    return base_role_weight * (compliance_score / 100) * evidence_strength * (1 - hallucination_risk)


def run_consensus(votes: Dict[str, Dict], threshold: float = 0.80) -> ConsensusResult:
    if not votes:
        return ConsensusResult(
            claim_id="",
            accepted=False,
            consensus_confidence=0.0,
            weighted_votes={},
            dissent=[],
        )

    weighted_votes: Dict[str, float] = {}
    total_weight = 0.0
    accept_weight = 0.0
    dissenters: List[str] = []
    dissent_reasons: List[str] = []

    for agent_id, vote in votes.items():
        w = float(vote.get("weight", 0.0))
        accept = bool(vote.get("accept", False))
        reason = str(vote.get("reason", ""))
        weighted_votes[agent_id] = w
        total_weight += w
        if accept:
            accept_weight += w
        else:
            dissenters.append(agent_id)
            if reason:
                dissent_reasons.append(reason)

    consensus_confidence = (accept_weight / total_weight) if total_weight > 0 else 0.0
    accepted = consensus_confidence >= threshold

    dissent: List[DissentRecord] = []
    if dissenters:
        risk = "high" if len(dissenters) > len(votes) / 2 else ("medium" if len(dissenters) > 1 else "low")
        dissent.append(
            DissentRecord(
                claim_id="",
                dissenters=dissenters,
                reason="; ".join(dissent_reasons) if dissent_reasons else "No reason provided",
                risk_level=risk,
            )
        )

    return ConsensusResult(
        claim_id="",
        accepted=accepted,
        consensus_confidence=consensus_confidence,
        weighted_votes=weighted_votes,
        dissent=dissent,
    )


def validate_synthesis(accepted_claims: List, synthesis_text: str) -> List[str]:
    violations: List[str] = []
    accepted_ids = set()
    for c in accepted_claims:
        if isinstance(c, dict):
            cid = c.get("claim_id", "")
            if cid:
                accepted_ids.add(cid)
        elif isinstance(c, str):
            accepted_ids.add(c)

    if "CLAIM:" in synthesis_text:
        for line in synthesis_text.split("\n"):
            line_stripped = line.strip()
            if line_stripped.startswith("CLAIM:"):
                claim_ref = line_stripped.split("CLAIM:", 1)[1].strip().split()[0] if line_stripped.split("CLAIM:", 1)[1].strip() else ""
                if claim_ref and claim_ref not in accepted_ids:
                    violations.append(f"New claim introduced not in accepted set: {claim_ref}")

    if "VERIFIED" in synthesis_text and "evidence:" not in synthesis_text.lower():
        violations.append("Used VERIFIED label without evidence")

    has_analytic = "ANALYTIC" in synthesis_text
    has_exploratory = "EXPLORATORY" in synthesis_text
    if has_analytic and has_exploratory:
        violations.append("Mixed modes in final sections")

    if "BLOCKED" in synthesis_text:
        violations.append("Left blocked requests unresolved")

    return violations


def get_role_prompt_suffix(role: CognitiveRole) -> str:
    suffixes = {
        CognitiveRole.ARCHITECT: "Propose solution structure and produce initial claim ledger with evidence pointers.",
        CognitiveRole.SKEPTIC: "Attack assumptions, find missing evidence, push downgrades on unverified claims.",
        CognitiveRole.EVIDENCE_VALIDATOR: "Verify pointers, receipts, citations. Score evidence quality for each claim.",
        CognitiveRole.CONSTRAINT_ENFORCER: "Check governance, budgets, allowlists, retention. Flag policy violations.",
        CognitiveRole.CREATIVE_DIVERGENCE: "EXPLORATORY MODE: Generate novel hypotheses with explicit test paths.",
        CognitiveRole.SYNTHESIZER: "Merge outputs only from accepted claims. Never invent new claims.",
        CognitiveRole.REFLEXIVE_AUDITOR: "META-COGNITIVE MODE: Review CS/HR trends, detect drift, propose parameter adjustments. Output meta-claims about system performance.",
        CognitiveRole.RED_TEAM: "ADVERSARIAL MODE: Test for prompt injection, evidence fabrication, constraint bypass. Report vulnerabilities without exploiting them.",
        CognitiveRole.FALSIFIER: "FALSIFICATION MODE: Actively seek counterexamples and logical flaws in proposals. Stress-test claims. Successful falsification strengthens surviving claims.",
    }
    return suffixes.get(role, "")


def get_debate_round_instruction(round: DebateRound) -> str:
    instructions = {
        DebateRound.GENESIS_BINDING: "Round 0: Verify IntentVector, GovernanceEnvelope, and CapabilityPlan exist. Bind all agents to governance context before proceeding.",
        DebateRound.PROPOSAL: "Round 1: Architect proposes plan with claims. Each claim must have claim_id, label, confidence, evidence_pointers, premises, and failure_modes.",
        DebateRound.ADVERSARIAL_AUDIT: "Round 2: Skeptic and Constraint Enforcer enumerate violations, unknowns, and unsupported assumptions. Downgrade unverified claims.",
        DebateRound.EVIDENCE_VALIDATION: "Round 3: Validator confirms pointers and receipts. Assign evidence_strength score to each claim. Reject claims with no valid evidence.",
        DebateRound.CREATIVE_DIVERGENCE: "Round 4: Creative agent produces labeled HYPOTHESIS entries with explicit test paths. All outputs marked EXPLORATORY.",
        DebateRound.CONSENSUS: "Round 5: Compute weighted votes per claim. Accept claims meeting threshold. Record all dissent with reasons and risk levels.",
        DebateRound.SYNTHESIS: "Round 6: Synthesizer outputs final result from accepted claims only. No new claims. No unverified labels. No mixed modes.",
        DebateRound.ADVERSARIAL_TESTING: "Round 7: Red Team runs adversarial probes against the synthesis output. Falsifier attempts to break surviving claims. Reflexive Auditor logs meta-claims about cycle quality.",
    }
    return instructions.get(round, "")


def map_wave_to_cognitive_roles(wave_idx: int) -> List[CognitiveRole]:
    if wave_idx == 0:
        return [CognitiveRole.ARCHITECT, CognitiveRole.SKEPTIC, CognitiveRole.CONSTRAINT_ENFORCER, CognitiveRole.FALSIFIER]
    elif wave_idx == 1:
        return [
            CognitiveRole.ARCHITECT,
            CognitiveRole.EVIDENCE_VALIDATOR,
            CognitiveRole.CONSTRAINT_ENFORCER,
            CognitiveRole.CREATIVE_DIVERGENCE,
            CognitiveRole.FALSIFIER,
        ]
    elif wave_idx == 2:
        return [
            CognitiveRole.ARCHITECT,
            CognitiveRole.SKEPTIC,
            CognitiveRole.EVIDENCE_VALIDATOR,
            CognitiveRole.CREATIVE_DIVERGENCE,
            CognitiveRole.RED_TEAM,
        ]
    else:
        return [
            CognitiveRole.ARCHITECT,
            CognitiveRole.SKEPTIC,
            CognitiveRole.EVIDENCE_VALIDATOR,
            CognitiveRole.CONSTRAINT_ENFORCER,
            CognitiveRole.CREATIVE_DIVERGENCE,
            CognitiveRole.SYNTHESIZER,
            CognitiveRole.RED_TEAM,
            CognitiveRole.REFLEXIVE_AUDITOR,
        ]
