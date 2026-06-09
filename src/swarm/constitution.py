import logging
import re
import uuid
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _semantic_validate_claim(claim_text: str, premises: List[str]) -> float:
    """
    Semantic premise→claim validation via the memory substrate KG.

    - Checks each premise against the KG for contradiction with the claim.
    - Checks the claim itself for support from VERIFIED KG entries.
    - Returns a confidence delta in [-0.20, +0.10] to apply on top of the
      parsed confidence value.  Always returns 0.0 if substrate is unavailable
      so parsing is never blocked.
    """
    if not premises and not claim_text:
        return 0.0
    try:
        from src.memory import get_substrate
        substrate = get_substrate()

        # Check each premise for contradiction with the claim
        contradicting_premises = 0
        for premise in premises:
            contras = substrate.find_contradictions(premise, threshold=0.15)
            for c in contras:
                # A KG contradiction involving the claim's topic is a warning
                if claim_text[:80].lower() in c.get("text", "").lower():
                    contradicting_premises += 1
                    break

        # Check claim for contradiction with known VERIFIED facts
        claim_contras = substrate.find_contradictions(claim_text, threshold=0.20)
        claim_contradicted = bool(claim_contras)

        # Check for KG-support (VERIFIED recalls)
        recalled = substrate.recall(claim_text, top_k=3, min_confidence=0.7)
        kg_supported = any(e.verified for e in recalled)

        if claim_contradicted or contradicting_premises > 0:
            return -0.20
        if kg_supported:
            return 0.10
        return 0.0
    except Exception:
        return 0.0


class EpistemicCategory(Enum):
    VERIFIED = "verified"
    DERIVED = "derived"
    HYPOTHESIS = "hypothesis"
    UNKNOWN = "unknown"


@dataclass
class Claim:
    text: str
    label: EpistemicCategory
    confidence: float
    claim_id: str = ""
    evidence_pointers: List[str] = field(default_factory=list)
    premises: List[str] = field(default_factory=list)
    failure_modes: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.claim_id:
            self.claim_id = f"CLM-{uuid.uuid4().hex[:6].upper()}"
        self.confidence = max(0.0, min(1.0, self.confidence))


class ClaimLedger:
    def __init__(self):
        self._claims: Dict[str, Claim] = {}
        self._counter: int = 0

    def add_claim(
        self,
        text: str,
        label: EpistemicCategory = EpistemicCategory.HYPOTHESIS,
        confidence: float = 0.5,
        evidence_pointers: Optional[List[str]] = None,
        premises: Optional[List[str]] = None,
        failure_modes: Optional[List[str]] = None,
    ) -> Claim:
        self._counter += 1
        claim_id = f"CLM-{self._counter:03d}"

        # Semantic premise→claim validation: adjust confidence based on KG
        # entailment/contradiction check.  Never raises — returns delta 0.0
        # if substrate unavailable so claim parsing is never blocked.
        sem_delta = _semantic_validate_claim(text, premises or [])
        adjusted_confidence = max(0.0, min(1.0, confidence + sem_delta))
        if sem_delta != 0.0:
            logger.debug(
                "Claim %s semantic delta %.2f (%.2f→%.2f): %s",
                claim_id, sem_delta, confidence, adjusted_confidence, text[:80],
            )

        claim = Claim(
            claim_id=claim_id,
            text=text,
            label=label,
            confidence=adjusted_confidence,
            evidence_pointers=evidence_pointers or [],
            premises=premises or [],
            failure_modes=failure_modes or [],
        )
        self._claims[claim_id] = claim
        return claim

    def get_claim(self, claim_id: str) -> Optional[Claim]:
        return self._claims.get(claim_id)

    def get_load_bearing_claims(self) -> List[Claim]:
        keywords = [
            "architecture", "architectural",
            "factual", "fact",
            "safety", "secure", "security",
            "cost", "budget", "price",
            "critical", "foundational",
        ]
        results = []
        for claim in self._claims.values():
            lower = claim.text.lower()
            if any(kw in lower for kw in keywords):
                results.append(claim)
            elif claim.label == EpistemicCategory.VERIFIED and claim.confidence >= 0.8:
                results.append(claim)
        return results

    def to_yaml_str(self) -> str:
        lines = ["CLAIM_LEDGER:"]
        for claim in self._claims.values():
            lines.append(f"  - claim_id: {claim.claim_id}")
            lines.append(f"    label: {claim.label.value}")
            lines.append(f"    confidence: {claim.confidence}")
            lines.append(f"    text: \"{claim.text}\"")
            if claim.evidence_pointers:
                lines.append(f"    evidence: {claim.evidence_pointers}")
            if claim.premises:
                lines.append(f"    premises: {claim.premises}")
            if claim.failure_modes:
                lines.append(f"    failure_modes: {claim.failure_modes}")
        return "\n".join(lines)

    @classmethod
    def from_text(cls, raw_text: str) -> "ClaimLedger":
        ledger = cls()
        claim_pattern = re.compile(
            r"claim_id\s*:\s*(CLM-\S+).*?"
            r"label\s*:\s*(\w+).*?"
            r"confidence\s*:\s*([\d.]+).*?"
            r"text\s*:\s*[\"']?(.+?)[\"']?\s*$",
            re.MULTILINE | re.DOTALL,
        )

        block_pattern = re.compile(
            r"-\s*claim_id\s*:\s*(CLM-\S+)",
            re.MULTILINE,
        )
        blocks = list(block_pattern.finditer(raw_text))

        if blocks:
            for i, match in enumerate(blocks):
                start = match.start()
                end = blocks[i + 1].start() if i + 1 < len(blocks) else len(raw_text)
                chunk = raw_text[start:end]

                cid_m = re.search(r"claim_id\s*:\s*(CLM-\S+)", chunk)
                label_m = re.search(r"label\s*:\s*(\w+)", chunk)
                conf_m = re.search(r"confidence\s*:\s*([\d.]+)", chunk)
                text_m = re.search(r"text\s*:\s*[\"']?(.+?)[\"']?\s*$", chunk, re.MULTILINE)

                cid = cid_m.group(1) if cid_m else None
                label_str = label_m.group(1).lower() if label_m else "hypothesis"
                conf = float(conf_m.group(1)) if conf_m else 0.5
                text_val = text_m.group(1).strip() if text_m else chunk.strip()[:200]

                try:
                    label = EpistemicCategory(label_str)
                except ValueError:
                    label = EpistemicCategory.HYPOTHESIS

                evidence_m = re.search(r"evidence\s*:\s*\[(.+?)\]", chunk)
                evidence = [e.strip().strip("'\"") for e in evidence_m.group(1).split(",")] if evidence_m else []

                premises_m = re.search(r"premises\s*:\s*\[(.+?)\]", chunk)
                premises = [p.strip().strip("'\"") for p in premises_m.group(1).split(",")] if premises_m else []

                failure_m = re.search(r"failure_modes\s*:\s*\[(.+?)\]", chunk)
                failure_modes = [f.strip().strip("'\"") for f in failure_m.group(1).split(",")] if failure_m else []

                claim = ledger.add_claim(
                    text=text_val,
                    label=label,
                    confidence=conf,
                    evidence_pointers=evidence,
                    premises=premises,
                    failure_modes=failure_modes,
                )
                if cid:
                    ledger._claims.pop(claim.claim_id, None)
                    claim.claim_id = cid
                    ledger._claims[cid] = claim
        else:
            stripped = raw_text.strip()
            if stripped:
                ledger.add_claim(
                    text=stripped[:500],
                    label=EpistemicCategory.HYPOTHESIS,
                    confidence=0.3,
                )

        return ledger


class ReasoningMode(Enum):
    ANALYTIC = "analytic"
    EXPLORATORY = "exploratory"


@dataclass
class IntentBinding:
    objective_id: str
    success_criteria: List[str] = field(default_factory=list)
    active_constraints: List[str] = field(default_factory=list)
    policy_class: str = "standard"


class StopCondition:
    @staticmethod
    def check_evidence_gap(claims: List[Claim]) -> bool:
        if not claims:
            return True
        unknown_count = sum(1 for c in claims if c.label == EpistemicCategory.UNKNOWN)
        return unknown_count > len(claims) * 0.5

    @staticmethod
    def check_constraint_conflict(constraints: List[str]) -> bool:
        if len(constraints) < 2:
            return False
        negation_pairs = []
        for i, c1 in enumerate(constraints):
            for c2 in constraints[i + 1:]:
                c1_lower = c1.lower().strip()
                c2_lower = c2.lower().strip()
                if c1_lower.startswith("no ") and c1_lower[3:] in c2_lower:
                    return True
                if c2_lower.startswith("no ") and c2_lower[3:] in c1_lower:
                    return True
                if c1_lower.startswith("not ") and c1_lower[4:] in c2_lower:
                    return True
                if c2_lower.startswith("not ") and c2_lower[4:] in c1_lower:
                    return True
        return False

    @staticmethod
    def check_policy_block(policy_decisions: List[str]) -> bool:
        block_signals = ["blocked", "denied", "rejected", "forbidden", "prohibited"]
        for decision in policy_decisions:
            lower = decision.lower()
            if any(signal in lower for signal in block_signals):
                return True
        return False

    @staticmethod
    def check_consensus_failure(consensus_scores: List[float]) -> bool:
        if not consensus_scores:
            return True
        avg = sum(consensus_scores) / len(consensus_scores)
        return avg < 0.4


CONSTITUTION_RULES = (
    "AGENT CONSTITUTION:\n"
    "1. Every claim must carry an epistemic label (verified/derived/hypothesis/unknown) and confidence [0-1].\n"
    "2. No claim may be presented as fact without evidence pointers.\n"
    "3. Load-bearing claims (architecture, safety, cost) require confidence >= 0.8 or explicit uncertainty.\n"
    "4. Agents must declare reasoning mode: ANALYTIC (deductive) or EXPLORATORY (generative).\n"
    "5. Banned phrases without verification: definitely, guaranteed, proven, obviously, clearly, everyone knows, as an expert.\n"
    "6. Stop on: >50% unknown claims, constraint conflicts, policy blocks, consensus < 0.4.\n"
    "7. All outputs must include CLAIM_LEDGER, MODE, and EVIDENCE sections.\n"
    "8. Intent binding: every response must reference its objective_id and active constraints."
)

CONSTITUTION_OUTPUT_SCHEMA = (
    "REQUIRED OUTPUT FORMAT:\n"
    "CLAIM_LEDGER:\n"
    "  - claim_id: CLM-NNN\n"
    "    label: verified|derived|hypothesis|unknown\n"
    "    confidence: 0.0-1.0\n"
    "    text: \"<claim content>\"\n"
    "    evidence: [<pointers>]\n"
    "    premises: [<claim_ids>]\n"
    "    failure_modes: [<modes>]\n"
    "MODE: ANALYTIC|EXPLORATORY\n"
    "EVIDENCE:\n"
    "  - source: <reference>\n"
    "    type: log|test|doc|code|observation\n"
    "    snippet: \"<relevant excerpt>\"\n"
    "INTENT:\n"
    "  objective_id: <id>\n"
    "  constraints: [<active constraints>]"
)

BANNED_PHRASES = [
    "definitely",
    "guaranteed",
    "proven",
    "obviously",
    "clearly",
    "everyone knows",
    "as an expert",
]


def validate_language(text: str) -> List[str]:
    violations = []
    lower = text.lower()
    for phrase in BANNED_PHRASES:
        if phrase in lower:
            violations.append(f"Banned phrase detected: '{phrase}'")
    return violations
