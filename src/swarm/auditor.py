import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class MetaClaim:
    claim_id: str
    category: str
    description: str
    severity: str
    evidence: List[str]
    proposed_action: str
    timestamp: float

    def __post_init__(self) -> None:
        if not self.claim_id:
            self.claim_id = f"MC-{uuid.uuid4().hex[:6].upper()}"
        if self.category not in ("drift", "performance", "anomaly", "recommendation"):
            self.category = "anomaly"
        if self.severity not in ("info", "warning", "critical"):
            self.severity = "info"


@dataclass
class PerformanceEntry:
    wave_idx: int
    avg_cs: float
    avg_hr: float
    agent_count: int
    consensus_confidence: float
    dissent_count: int
    timestamp: float


class PerformanceLedger:
    def __init__(self) -> None:
        self._entries: List[PerformanceEntry] = []

    def record(
        self,
        wave_idx: int,
        avg_cs: float,
        avg_hr: float,
        agent_count: int,
        consensus_confidence: float,
        dissent_count: int,
    ) -> PerformanceEntry:
        try:
            entry = PerformanceEntry(
                wave_idx=wave_idx,
                avg_cs=avg_cs,
                avg_hr=avg_hr,
                agent_count=agent_count,
                consensus_confidence=consensus_confidence,
                dissent_count=dissent_count,
                timestamp=time.time(),
            )
            self._entries.append(entry)
            return entry
        except Exception:
            entry = PerformanceEntry(
                wave_idx=wave_idx,
                avg_cs=0.0,
                avg_hr=0.0,
                agent_count=0,
                consensus_confidence=0.0,
                dissent_count=0,
                timestamp=time.time(),
            )
            self._entries.append(entry)
            return entry

    def get_trend(self, window: int = 5) -> Dict[str, str]:
        try:
            if len(self._entries) < 2:
                return {
                    "cs_trend": "stable",
                    "hr_trend": "stable",
                    "consensus_trend": "stable",
                }

            recent = self._entries[-window:]
            cs_vals = [e.avg_cs for e in recent]
            hr_vals = [e.avg_hr for e in recent]
            con_vals = [e.consensus_confidence for e in recent]

            def _direction(vals: List[float]) -> str:
                if len(vals) < 2:
                    return "stable"
                deltas = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
                avg_delta = sum(deltas) / len(deltas)
                if avg_delta > 2.0:
                    return "improving"
                if avg_delta < -2.0:
                    return "degrading"
                return "stable"

            def _direction_inverse(vals: List[float]) -> str:
                if len(vals) < 2:
                    return "stable"
                deltas = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
                avg_delta = sum(deltas) / len(deltas)
                if avg_delta < -0.02:
                    return "improving"
                if avg_delta > 0.02:
                    return "degrading"
                return "stable"

            return {
                "cs_trend": _direction(cs_vals),
                "hr_trend": _direction_inverse(hr_vals),
                "consensus_trend": _direction(con_vals),
            }
        except Exception:
            return {
                "cs_trend": "stable",
                "hr_trend": "stable",
                "consensus_trend": "stable",
            }

    def get_summary(self) -> str:
        try:
            if not self._entries:
                return "No performance data recorded yet."

            total = len(self._entries)
            avg_cs = sum(e.avg_cs for e in self._entries) / total
            avg_hr = sum(e.avg_hr for e in self._entries) / total
            avg_con = sum(e.consensus_confidence for e in self._entries) / total
            total_dissent = sum(e.dissent_count for e in self._entries)
            trend = self.get_trend()

            return (
                f"Performance Summary ({total} waves recorded):\n"
                f"  Avg Compliance Score: {avg_cs:.1f}\n"
                f"  Avg Hallucination Risk: {avg_hr:.3f}\n"
                f"  Avg Consensus Confidence: {avg_con:.3f}\n"
                f"  Total Dissent Events: {total_dissent}\n"
                f"  CS Trend: {trend['cs_trend']}\n"
                f"  HR Trend: {trend['hr_trend']}\n"
                f"  Consensus Trend: {trend['consensus_trend']}"
            )
        except Exception:
            return "Error generating performance summary."

    def get_anomalies(self) -> List[PerformanceEntry]:
        try:
            return [e for e in self._entries if e.avg_cs < 50 or e.avg_hr > 0.6]
        except Exception:
            return []


@dataclass
class ShadowTestResult:
    proposal: str
    baseline_score: float
    adjusted_score: float
    improvement: float
    approved: bool


class ReflexiveAuditor:
    def __init__(self) -> None:
        self.ledger = PerformanceLedger()
        self._meta_claims: List[MetaClaim] = []
        self._claim_counter: int = 0

    def _make_claim(
        self,
        category: str,
        description: str,
        severity: str,
        evidence: List[str],
        proposed_action: str,
    ) -> MetaClaim:
        self._claim_counter += 1
        claim = MetaClaim(
            claim_id=f"MC-{self._claim_counter:04d}",
            category=category,
            description=description,
            severity=severity,
            evidence=evidence,
            proposed_action=proposed_action,
            timestamp=time.time(),
        )
        self._meta_claims.append(claim)
        self._raise_governance_proposal(claim)
        return claim

    def _raise_governance_proposal(self, claim: "MetaClaim") -> None:
        """Convert each MetaClaim into a formal governance proposal.

        The auditor never silently mutates swarm parameters. Instead every
        meta-claim becomes a typed proposal (with an Origin and a Track)
        that must be voted on and enacted before any parameter change takes
        effect. Failures are suppressed so a DB outage never breaks the swarm.
        """
        try:
            from src.swarm.governance_engine import GovernanceEngine
            GovernanceEngine.from_meta_claim(claim)
        except Exception as e:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "Failed to raise governance proposal for claim %s: %s", claim.claim_id, e
            )

    def audit_wave(
        self,
        wave_idx: int,
        agent_results: List[Dict],
        compliance_scores: List[float],
        dissent_log: List[Dict],
    ) -> List[MetaClaim]:
        generated: List[MetaClaim] = []

        try:
            avg_cs = sum(compliance_scores) / max(1, len(compliance_scores)) if compliance_scores else 0.0
            hr_values = []
            for r in agent_results:
                try:
                    hr_val = float(r.get("hallucination_risk", 0.0))
                    hr_values.append(hr_val)
                except (ValueError, TypeError):
                    pass
            avg_hr = sum(hr_values) / max(1, len(hr_values)) if hr_values else 0.0

            con_values = []
            for r in agent_results:
                try:
                    con_val = float(r.get("consensus_confidence", 0.0))
                    con_values.append(con_val)
                except (ValueError, TypeError):
                    pass
            avg_con = sum(con_values) / max(1, len(con_values)) if con_values else 0.0

            self.ledger.record(
                wave_idx=wave_idx,
                avg_cs=avg_cs,
                avg_hr=avg_hr,
                agent_count=len(agent_results),
                consensus_confidence=avg_con,
                dissent_count=len(dissent_log),
            )
        except Exception:
            self.ledger.record(
                wave_idx=wave_idx,
                avg_cs=0.0,
                avg_hr=0.0,
                agent_count=len(agent_results) if agent_results else 0,
                consensus_confidence=0.0,
                dissent_count=len(dissent_log) if dissent_log else 0,
            )

        try:
            trend = self.ledger.get_trend()
            if trend["cs_trend"] == "degrading":
                claim = self._make_claim(
                    category="performance",
                    description=f"Compliance score degradation detected over recent waves (wave {wave_idx})",
                    severity="warning",
                    evidence=[f"CS trend: {trend['cs_trend']}", f"Current avg CS: {avg_cs:.1f}"],
                    proposed_action="Review agent prompts and increase evidence requirements",
                )
                generated.append(claim)

            if avg_cs < 50:
                claim = self._make_claim(
                    category="performance",
                    description=f"Critical compliance score drop at wave {wave_idx}: {avg_cs:.1f}",
                    severity="critical",
                    evidence=[f"avg_cs={avg_cs:.1f}", "Threshold: 50"],
                    proposed_action="Halt pipeline and require manual review",
                )
                generated.append(claim)
        except Exception:
            pass

        try:
            trend = self.ledger.get_trend()
            if trend["hr_trend"] == "degrading":
                claim = self._make_claim(
                    category="anomaly",
                    description=f"Hallucination risk escalation detected (wave {wave_idx})",
                    severity="warning",
                    evidence=[f"HR trend: {trend['hr_trend']}", f"Current avg HR: {avg_hr:.3f}"],
                    proposed_action="Add skeptic agent and increase verification rounds",
                )
                generated.append(claim)

            if avg_hr > 0.6:
                claim = self._make_claim(
                    category="anomaly",
                    description=f"Critical hallucination risk at wave {wave_idx}: {avg_hr:.3f}",
                    severity="critical",
                    evidence=[f"avg_hr={avg_hr:.3f}", "Threshold: 0.6"],
                    proposed_action="Block synthesis and force evidence validation",
                )
                generated.append(claim)
        except Exception:
            pass

        try:
            if agent_results and len(agent_results) >= 2:
                outputs = []
                for r in agent_results:
                    text = str(r.get("output", r.get("text", r.get("response", ""))))
                    outputs.append(text)

                lengths = [len(o) for o in outputs]
                short_count = sum(1 for l in lengths if l < 50)
                if short_count > len(outputs) * 0.6:
                    claim = self._make_claim(
                        category="drift",
                        description=f"Low engagement detected: {short_count}/{len(outputs)} agents produced short outputs at wave {wave_idx}",
                        severity="warning",
                        evidence=[f"Short outputs: {short_count}/{len(outputs)}", f"Avg length: {sum(lengths)/max(1,len(lengths)):.0f} chars"],
                        proposed_action="Increase prompt specificity or add creative divergence agent",
                    )
                    generated.append(claim)

                if len(set(outputs)) == 1 and len(outputs) > 1:
                    claim = self._make_claim(
                        category="drift",
                        description=f"All agents produced identical outputs at wave {wave_idx}",
                        severity="warning",
                        evidence=["All outputs identical", f"Agent count: {len(outputs)}"],
                        proposed_action="Diversify agent roles or increase temperature",
                    )
                    generated.append(claim)
        except Exception:
            pass

        try:
            if agent_results:
                roles: List[str] = []
                for r in agent_results:
                    role = str(r.get("role", r.get("cognitive_role", "unknown")))
                    roles.append(role)

                if roles:
                    role_counts = Counter(roles)
                    total_agents = len(roles)
                    for role_name, count in role_counts.items():
                        if count > total_agents * 0.5 and total_agents >= 3:
                            claim = self._make_claim(
                                category="anomaly",
                                description=f"Role imbalance: '{role_name}' dominates with {count}/{total_agents} agents at wave {wave_idx}",
                                severity="info",
                                evidence=[f"Role '{role_name}': {count}/{total_agents}", f"Distribution: {dict(role_counts)}"],
                                proposed_action=f"Reduce {role_name} count and add underrepresented roles",
                            )
                            generated.append(claim)
        except Exception:
            pass

        try:
            if dissent_log and len(dissent_log) >= 3:
                dissent_reasons: List[str] = []
                for d in dissent_log:
                    reason = str(d.get("reason", d.get("description", "")))
                    if reason:
                        dissent_reasons.append(reason)

                if len(dissent_reasons) >= 3:
                    claim = self._make_claim(
                        category="drift",
                        description=f"Persistent dissent pattern: {len(dissent_reasons)} dissent events at wave {wave_idx}",
                        severity="warning",
                        evidence=[f"Dissent count: {len(dissent_reasons)}"] + dissent_reasons[:3],
                        proposed_action="Investigate root cause of dissent and consider policy revision",
                    )
                    generated.append(claim)

            entries = self.ledger._entries
            if len(entries) >= 3:
                recent_dissent = [e.dissent_count for e in entries[-3:]]
                if all(d >= 2 for d in recent_dissent):
                    claim = self._make_claim(
                        category="drift",
                        description=f"Sustained dissent across last 3 waves (counts: {recent_dissent})",
                        severity="warning",
                        evidence=[f"Recent dissent counts: {recent_dissent}"],
                        proposed_action="Re-evaluate consensus threshold or agent composition",
                    )
                    generated.append(claim)
        except Exception:
            pass

        return generated

    def get_meta_claims(self, severity: Optional[str] = None) -> List[MetaClaim]:
        try:
            if severity is None:
                return list(self._meta_claims)
            return [c for c in self._meta_claims if c.severity == severity]
        except Exception:
            return []

    def propose_adjustments(self) -> List[Dict]:
        adjustments: List[Dict] = []
        try:
            seen_actions: set = set()
            for claim in self._meta_claims:
                if claim.proposed_action in seen_actions:
                    continue
                seen_actions.add(claim.proposed_action)

                adjustment: Dict = {
                    "source_claim": claim.claim_id,
                    "category": claim.category,
                    "severity": claim.severity,
                    "action": claim.proposed_action,
                }

                if "skeptic" in claim.proposed_action.lower():
                    adjustment["parameter"] = "skeptic_weight"
                    adjustment["direction"] = "increase"
                elif "evidence" in claim.proposed_action.lower():
                    adjustment["parameter"] = "evidence_threshold"
                    adjustment["direction"] = "increase"
                elif "halt" in claim.proposed_action.lower():
                    adjustment["parameter"] = "pipeline_state"
                    adjustment["direction"] = "halt"
                elif "temperature" in claim.proposed_action.lower():
                    adjustment["parameter"] = "temperature"
                    adjustment["direction"] = "increase"
                elif "reduce" in claim.proposed_action.lower() and "debate" in claim.proposed_action.lower():
                    adjustment["parameter"] = "debate_rounds"
                    adjustment["direction"] = "decrease"
                elif "creative" in claim.proposed_action.lower():
                    adjustment["parameter"] = "creative_divergence_weight"
                    adjustment["direction"] = "increase"
                elif "consensus" in claim.proposed_action.lower():
                    adjustment["parameter"] = "consensus_threshold"
                    adjustment["direction"] = "adjust"
                else:
                    adjustment["parameter"] = "general"
                    adjustment["direction"] = "review"

                adjustments.append(adjustment)
        except Exception:
            pass

        return adjustments

    def get_status(self) -> Dict:
        try:
            by_severity: Dict[str, int] = {"info": 0, "warning": 0, "critical": 0}
            for claim in self._meta_claims:
                if claim.severity in by_severity:
                    by_severity[claim.severity] += 1

            return {
                "total_meta_claims": len(self._meta_claims),
                "by_severity": by_severity,
                "latest_trend": self.ledger.get_trend(),
                "proposed_adjustments": self.propose_adjustments(),
                "anomaly_count": len(self.ledger.get_anomalies()),
                "waves_recorded": len(self.ledger._entries),
            }
        except Exception:
            return {
                "total_meta_claims": 0,
                "by_severity": {"info": 0, "warning": 0, "critical": 0},
                "latest_trend": {"cs_trend": "stable", "hr_trend": "stable", "consensus_trend": "stable"},
                "proposed_adjustments": [],
                "anomaly_count": 0,
                "waves_recorded": 0,
            }

    def to_summary(self) -> str:
        try:
            status = self.get_status()
            trend = status["latest_trend"]
            adjustments = status["proposed_adjustments"]

            lines = [
                f"Reflexive Auditor Status:",
                f"  Meta-Claims: {status['total_meta_claims']} (info={status['by_severity']['info']}, warn={status['by_severity']['warning']}, crit={status['by_severity']['critical']})",
                f"  Waves: {status['waves_recorded']}",
                f"  Trends: CS={trend['cs_trend']}, HR={trend['hr_trend']}, Consensus={trend['consensus_trend']}",
                f"  Anomalies: {status['anomaly_count']}",
                f"  Proposed Adjustments: {len(adjustments)}",
            ]

            if adjustments:
                for adj in adjustments[:3]:
                    lines.append(f"    - [{adj.get('severity', '?')}] {adj.get('action', 'N/A')}")
                if len(adjustments) > 3:
                    lines.append(f"    ... and {len(adjustments) - 3} more")

            return "\n".join(lines)
        except Exception:
            return "Reflexive Auditor: Error generating summary."
