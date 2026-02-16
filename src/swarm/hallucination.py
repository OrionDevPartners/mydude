from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


@dataclass
class HallucinationFeatures:
    unlabeled_ratio: float = 0.0
    unevidenced_ratio: float = 0.0
    mode_mixing_rate: float = 0.0
    constraint_pressure: float = 0.0
    novelty_pressure: float = 0.0
    external_dependency: float = 0.0
    disagreement_index: float = 0.0
    overconfidence_delta: float = 0.0


def compute_hallucination_risk(features: HallucinationFeatures) -> float:
    hr = (
        0.22 * features.unlabeled_ratio
        + 0.22 * features.unevidenced_ratio
        + 0.12 * features.mode_mixing_rate
        + 0.10 * features.constraint_pressure
        + 0.10 * features.novelty_pressure
        + 0.10 * features.external_dependency
        + 0.08 * features.disagreement_index
        + 0.06 * features.overconfidence_delta
    )
    return max(0.0, min(1.0, hr))


class RiskTier(Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


def get_risk_tier(hr: float) -> RiskTier:
    if hr < 0.25:
        return RiskTier.LOW
    if hr < 0.50:
        return RiskTier.MEDIUM
    if hr < 0.75:
        return RiskTier.HIGH
    return RiskTier.CRITICAL


@dataclass
class RiskControlAction:
    tier: RiskTier
    increase_evidence: bool
    add_skeptic: bool
    block_synthesis: bool
    force_verification: bool
    halt_and_fail: bool
    description: str


def get_control_action(hr: float) -> RiskControlAction:
    tier = get_risk_tier(hr)
    if tier == RiskTier.LOW:
        return RiskControlAction(
            tier=tier,
            increase_evidence=False,
            add_skeptic=False,
            block_synthesis=False,
            force_verification=False,
            halt_and_fail=False,
            description="Normal flow; hallucination risk within acceptable bounds.",
        )
    if tier == RiskTier.MEDIUM:
        return RiskControlAction(
            tier=tier,
            increase_evidence=True,
            add_skeptic=True,
            block_synthesis=False,
            force_verification=False,
            halt_and_fail=False,
            description="Elevated risk; increasing evidence requirements and adding skeptic agent.",
        )
    if tier == RiskTier.HIGH:
        return RiskControlAction(
            tier=tier,
            increase_evidence=True,
            add_skeptic=True,
            block_synthesis=True,
            force_verification=True,
            halt_and_fail=False,
            description="High risk; blocking final synthesis, requiring tool verification or downgrading to hypothesis.",
        )
    return RiskControlAction(
        tier=tier,
        increase_evidence=True,
        add_skeptic=True,
        block_synthesis=True,
        force_verification=True,
        halt_and_fail=True,
        description="Critical risk; halting pipeline and emitting failure packet or forcing external validation.",
    )


@dataclass
class FailurePacket:
    reason: str
    blocked_claims: List[str]
    suggested_actions: List[str]
    risk_score: float


def build_features_from_compliance(
    compliance_report,
    provider_replies: list,
    constraint_budget_ratio: float,
) -> HallucinationFeatures:
    m = compliance_report.metrics
    total_claims = m.unlabeled_claims + (m.load_bearing_claims - max(0, m.load_bearing_claims - m.evidenced_claims))
    total_claims = max(total_claims, m.unlabeled_claims + m.load_bearing_claims)

    f1 = m.unlabeled_claims / max(1, total_claims)
    f1 = max(0.0, min(1.0, f1))

    lb = m.load_bearing_claims
    ev = m.evidenced_claims
    f2 = (lb - ev) / max(1, lb)
    f2 = max(0.0, min(1.0, f2))

    sections = max(1, 1 + m.mode_mixing_events)
    f3 = m.mode_mixing_events / max(1, sections)
    f3 = max(0.0, min(1.0, f3))

    f4 = max(0.0, min(1.0, constraint_budget_ratio))

    f5 = 0.3
    f6 = 0.3

    ok_count = sum(1 for r in provider_replies if r.ok)
    total_replies = max(1, len(provider_replies))
    fail_ratio = 1.0 - (ok_count / total_replies)
    f7 = max(0.0, min(1.0, fail_ratio))

    score_norm = compliance_report.score / 100.0
    f8 = max(0.0, min(1.0, 1.0 - score_norm))

    return HallucinationFeatures(
        unlabeled_ratio=f1,
        unevidenced_ratio=f2,
        mode_mixing_rate=f3,
        constraint_pressure=f4,
        novelty_pressure=f5,
        external_dependency=f6,
        disagreement_index=f7,
        overconfidence_delta=f8,
    )


@dataclass
class _HRRecord:
    hr: float
    wave: int
    agent: str


class HallucinationMonitor:
    def __init__(self) -> None:
        self._records: List[_HRRecord] = []

    def record(self, hr: float, wave: int, agent: str) -> None:
        self._records.append(_HRRecord(hr=hr, wave=wave, agent=agent))

    def get_trend(self) -> str:
        if len(self._records) < 2:
            return "stable"
        recent = [r.hr for r in self._records[-5:]]
        deltas = [recent[i + 1] - recent[i] for i in range(len(recent) - 1)]
        avg_delta = sum(deltas) / len(deltas)
        if avg_delta < -0.02:
            return "improving"
        if avg_delta > 0.02:
            return "degrading"
        return "stable"

    def get_average(self) -> float:
        if not self._records:
            return 0.0
        return sum(r.hr for r in self._records) / len(self._records)

    def should_abort(self) -> bool:
        if len(self._records) < 3:
            return False
        last_three = self._records[-3:]
        return all(get_risk_tier(r.hr) == RiskTier.CRITICAL for r in last_three)
