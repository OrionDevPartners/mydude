import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List


@dataclass
class ComplianceMetrics:
    unlabeled_claims: int = 0
    load_bearing_claims: int = 0
    evidenced_claims: int = 0
    constraint_violations: int = 0
    drift_events: int = 0
    mode_mixing_events: int = 0
    missing_required_fields: int = 0
    uncited_external_claims: int = 0


def compute_compliance_score(metrics: ComplianceMetrics) -> int:
    u = metrics.unlabeled_claims
    l = metrics.load_bearing_claims
    e = metrics.evidenced_claims
    c = metrics.constraint_violations
    d = metrics.drift_events
    m = metrics.mode_mixing_events
    r = metrics.missing_required_fields
    x = metrics.uncited_external_claims
    cs = 100 - 8 * u - 6 * (l - e) - 12 * c - 5 * d - 7 * m - 4 * r - 6 * x
    return max(0, min(100, cs))


class ComplianceTier(Enum):
    TRUSTED = "TRUSTED"
    REDUCED = "REDUCED"
    DRAFT = "DRAFT"
    REJECTED = "REJECTED"


def get_tier(score: int) -> ComplianceTier:
    if score >= 90:
        return ComplianceTier.TRUSTED
    if score >= 80:
        return ComplianceTier.REDUCED
    if score >= 65:
        return ComplianceTier.DRAFT
    return ComplianceTier.REJECTED


@dataclass
class ComplianceReport:
    score: int
    tier: ComplianceTier
    metrics: ComplianceMetrics
    violations: List[str] = field(default_factory=list)
    needs_correction: bool = False


_EPISTEMIC_LABELS = re.compile(
    r"\b(VERIFIED|DERIVED|HYPOTHESIS|UNKNOWN)\b", re.IGNORECASE
)
_CLAIM_PATTERN = re.compile(
    r"(?:^|\n)\s*[-*]?\s*(?:claim|assert|conclude|recommend|require|must|should|shall)\b",
    re.IGNORECASE,
)
_LOAD_BEARING_PATTERN = re.compile(
    r"\b(must|shall|require[ds]?|critical|mandatory|block(?:ing|er)?)\b", re.IGNORECASE
)
_EVIDENCE_PATTERN = re.compile(
    r"\b(evidence|source|ref|citation|see|per|according to|based on|cf\.|doc[s]?:)\b",
    re.IGNORECASE,
)
_EXTERNAL_FACT_PATTERN = re.compile(
    r"\b(according to|research shows|studies indicate|data from|report[s]? that|statistics show)\b",
    re.IGNORECASE,
)
_CITATION_PATTERN = re.compile(
    r"(\[[\d\w]+\]|\(https?://[^\s)]+\)|https?://[^\s]+|doi:\S+|arxiv:\S+)", re.IGNORECASE
)
_CLAIM_ID_PATTERN = re.compile(r"claim_id\s*[:=]", re.IGNORECASE)
_CONFIDENCE_PATTERN = re.compile(r"confidence\s*[:=]", re.IGNORECASE)
_INTENT_REF_PATTERN = re.compile(r"\b(OBJ|GOAL|INTENT|OBJECTIVE)[-_]?\d+\b", re.IGNORECASE)
_ANALYTIC_MARKERS = re.compile(
    r"\b(therefore|thus|hence|consequently|it follows|analysis shows|data indicates)\b",
    re.IGNORECASE,
)
_EXPLORATORY_MARKERS = re.compile(
    r"\b(perhaps|maybe|could be|might|possibly|what if|brainstorm|explore|speculate)\b",
    re.IGNORECASE,
)
_VIOLATION_KEYWORDS = re.compile(
    r"\b(bypass|skip policy|ignore constraint|override budget|no guardrail|unrestricted|allowlist violation)\b",
    re.IGNORECASE,
)


def _split_sections(text: str) -> List[str]:
    parts = re.split(r"\n{2,}|(?=^#{1,3}\s)", text, flags=re.MULTILINE)
    return [p.strip() for p in parts if p.strip()]


def analyze_agent_output(
    text: str, intent_refs: List[str], mode: str
) -> ComplianceReport:
    violations: List[str] = []
    sections = _split_sections(text)

    sentences = re.split(r"[.!?\n]+", text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 10]

    claim_count = 0
    unlabeled = 0
    for sent in sentences:
        if _CLAIM_PATTERN.search(sent) or _LOAD_BEARING_PATTERN.search(sent):
            claim_count += 1
            if not _EPISTEMIC_LABELS.search(sent):
                unlabeled += 1

    if not sentences:
        claim_count = max(1, len(sections))
        unlabeled = claim_count

    load_bearing = 0
    evidenced = 0
    for sent in sentences:
        if _LOAD_BEARING_PATTERN.search(sent):
            load_bearing += 1
            if _EVIDENCE_PATTERN.search(sent) or _CITATION_PATTERN.search(sent):
                evidenced += 1

    uncited_external = 0
    for sent in sentences:
        if _EXTERNAL_FACT_PATTERN.search(sent):
            if not _CITATION_PATTERN.search(sent):
                uncited_external += 1

    constraint_violations = len(_VIOLATION_KEYWORDS.findall(text))

    drift_events = 0
    has_any_intent_ref = bool(_INTENT_REF_PATTERN.search(text))
    explicit_refs_found = any(ref in text for ref in intent_refs) if intent_refs else False
    if not has_any_intent_ref and not explicit_refs_found:
        for section in sections:
            if len(section) > 40:
                drift_events += 1

    mode_mixing = 0
    mode_lower = mode.lower() if mode else ""
    for section in sections:
        has_analytic = bool(_ANALYTIC_MARKERS.search(section))
        has_exploratory = bool(_EXPLORATORY_MARKERS.search(section))
        if has_analytic and has_exploratory:
            mode_mixing += 1
        elif mode_lower == "analytic" and has_exploratory and not has_analytic:
            mode_mixing += 1
        elif mode_lower == "exploratory" and has_analytic and not has_exploratory:
            mode_mixing += 1

    claim_id_count = len(_CLAIM_ID_PATTERN.findall(text))
    confidence_count = len(_CONFIDENCE_PATTERN.findall(text))
    expected_fields = max(claim_count, 1)
    missing_claim_ids = max(0, expected_fields - claim_id_count)
    missing_confidence = max(0, expected_fields - confidence_count)
    missing_required = missing_claim_ids + missing_confidence

    if unlabeled > 0:
        violations.append(f"{unlabeled} claim(s) missing epistemic labels (VERIFIED/DERIVED/HYPOTHESIS/UNKNOWN)")
    unevidenced = load_bearing - evidenced
    if unevidenced > 0:
        violations.append(f"{unevidenced} load-bearing claim(s) lack evidence pointers")
    if constraint_violations > 0:
        violations.append(f"{constraint_violations} policy/budget/allowlist violation(s) detected")
    if drift_events > 0:
        violations.append(f"{drift_events} section(s) not bound to any intent reference")
    if mode_mixing > 0:
        violations.append(f"{mode_mixing} section(s) mix analytic and exploratory modes")
    if missing_required > 0:
        violations.append(f"{missing_required} missing required field(s) (claim_id, confidence)")
    if uncited_external > 0:
        violations.append(f"{uncited_external} external factual claim(s) without citations")

    metrics = ComplianceMetrics(
        unlabeled_claims=unlabeled,
        load_bearing_claims=load_bearing,
        evidenced_claims=evidenced,
        constraint_violations=constraint_violations,
        drift_events=drift_events,
        mode_mixing_events=mode_mixing,
        missing_required_fields=missing_required,
        uncited_external_claims=uncited_external,
    )

    score = compute_compliance_score(metrics)
    tier = get_tier(score)

    return ComplianceReport(
        score=score,
        tier=tier,
        metrics=metrics,
        violations=violations,
        needs_correction=score < 80,
    )


def compute_effective_weight(
    base_weight: float, compliance_score: int, evidence_quality: float
) -> float:
    return base_weight * (compliance_score / 100) * evidence_quality


def generate_correction_patch(report: ComplianceReport) -> str:
    if report.score >= 80:
        return ""

    lines = [
        f"COMPLIANCE CORRECTION PATCH (score={report.score}, tier={report.tier.value})",
        "=" * 60,
        "",
        "The following violations were detected and must be corrected:",
        "",
    ]

    m = report.metrics

    if m.unlabeled_claims > 0:
        lines.append(f"[RULE] Epistemic Labeling Required")
        lines.append(f"  {m.unlabeled_claims} claim(s) lack epistemic labels.")
        lines.append(f"  FIX: Tag each claim with one of: VERIFIED, DERIVED, HYPOTHESIS, UNKNOWN")
        lines.append("")

    unevidenced = m.load_bearing_claims - m.evidenced_claims
    if unevidenced > 0:
        lines.append(f"[RULE] Evidence Required for Load-Bearing Claims")
        lines.append(f"  {unevidenced} load-bearing claim(s) have no evidence pointer.")
        lines.append(f"  FIX: Add 'evidence:', 'source:', or citation reference for each")
        lines.append("")

    if m.constraint_violations > 0:
        lines.append(f"[RULE] Policy/Budget/Allowlist Compliance")
        lines.append(f"  {m.constraint_violations} violation(s) detected.")
        lines.append(f"  FIX: Remove or rephrase constraint-violating language; respect guardrails")
        lines.append("")

    if m.drift_events > 0:
        lines.append(f"[RULE] Intent Binding Required")
        lines.append(f"  {m.drift_events} section(s) not bound to any objective/intent reference.")
        lines.append(f"  FIX: Add OBJ-N or GOAL-N references linking each section to the intent")
        lines.append("")

    if m.mode_mixing_events > 0:
        lines.append(f"[RULE] Mode Separation Required")
        lines.append(f"  {m.mode_mixing_events} section(s) mix analytic and exploratory reasoning.")
        lines.append(f"  FIX: Separate analytic conclusions from exploratory speculation into distinct sections")
        lines.append("")

    if m.missing_required_fields > 0:
        lines.append(f"[RULE] Required Fields Missing")
        lines.append(f"  {m.missing_required_fields} missing required field(s).")
        lines.append(f"  FIX: Ensure each claim includes claim_id and confidence fields")
        lines.append("")

    if m.uncited_external_claims > 0:
        lines.append(f"[RULE] External Claims Require Citations")
        lines.append(f"  {m.uncited_external_claims} external factual claim(s) lack citations.")
        lines.append(f"  FIX: Add source URL, DOI, or reference for each external factual claim")
        lines.append("")

    lines.append("=" * 60)
    lines.append(f"Target: Achieve score >= 80 (current: {report.score})")
    return "\n".join(lines)
