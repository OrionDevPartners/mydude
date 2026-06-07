import os
import asyncio
import json
import re
import logging
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Tuple

from src.swarm.prompts import PORTER_SYSTEM_PROMPT, WORKER_SYSTEM_PROMPT
from src.swarm.utils import safe_json_dumps, clamp_list
from src.swarm.broker import CapabilityBroker

try:
    from src.swarm.jurisdiction import jurisdiction_metadata
except Exception:
    jurisdiction_metadata = None

from src.swarm.constitution import ClaimLedger, CONSTITUTION_RULES, validate_language, StopCondition, IntentBinding
from src.swarm.compliance import analyze_agent_output, compute_effective_weight, generate_correction_patch, ComplianceTier
from src.swarm.hallucination import (
    build_features_from_compliance, compute_hallucination_risk, get_control_action,
    HallucinationMonitor, FailurePacket, RiskTier
)
from src.swarm.contract import (
    CognitiveRole, map_wave_to_cognitive_roles, get_role_prompt_suffix,
    get_debate_round_instruction, DebateRound, run_consensus, validate_synthesis,
    compute_vote_weight, ROLE_BASE_WEIGHTS, DissentRecord
)

try:
    from src.swarm.provenance import ProvenanceTree, ConsistencyChecker
except Exception:
    ProvenanceTree = None
    ConsistencyChecker = None

try:
    from src.swarm.auditor import ReflexiveAuditor
except Exception:
    ReflexiveAuditor = None

try:
    from src.swarm.sentinel import GovernanceSentinel, RedTeamAgent
except Exception:
    GovernanceSentinel = None
    RedTeamAgent = None

try:
    from src.swarm.compliance import classify_novelty, consensus_confidence_boost, NoveltyClassification
except Exception:
    classify_novelty = None
    consensus_confidence_boost = None
    NoveltyClassification = None

logger = logging.getLogger(__name__)

WAVE_CONCURRENCY = int(os.getenv("WAVE_CONCURRENCY", "12"))
AGENTS_PER_WAVE = int(os.getenv("AGENTS_PER_WAVE", "60"))
MAX_WAVES = int(os.getenv("MAX_WAVES", "4"))
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "stub")


@dataclass
class Handoff:
    goal: str
    facts: List[str]
    decisions: List[str]
    tasks: List[str]
    risks: List[str]
    next: List[str]
    dissent: List[str] = field(default_factory=list)
    claim_ledger_summary: str = ""

    def compress_json(self, limit: int = 1500) -> str:
        payload = {
            "goal": self.goal,
            "facts": clamp_list(self.facts, 10),
            "decisions": clamp_list(self.decisions, 8),
            "tasks": clamp_list(self.tasks, 12),
            "risks": clamp_list(self.risks, 6),
            "next": clamp_list(self.next, 10),
        }
        s = json.dumps(payload, ensure_ascii=False)
        return s[:limit]


@dataclass
class AgentResult:
    agent: str
    wave: int
    result: str
    artifacts: List[str]
    checks: List[str]
    risks: List[str]
    handoff_json: str
    capability_requests: List[Tuple[str, Dict[str, Any]]]
    compliance_score: int = 100
    hallucination_risk: float = 0.0
    cognitive_role: str = ""


class LLM:
    def __init__(self):
        self._team = None
        self._jurisdiction: Optional[Dict[str, Any]] = None

    def apply_jurisdiction(self, meta: Dict[str, Any]) -> None:
        """Record a jurisdiction decision and propagate it to the live team."""
        self._jurisdiction = meta or {}
        if self._team is not None:
            self._team.apply_jurisdiction(
                exec_locus_pin=self._jurisdiction.get("exec_locus", "any"),
                cloud_shift_active=self._jurisdiction.get("cloud_shift_active", True),
            )

    def _get_team(self):
        if self._team is None and LLM_PROVIDER != "stub":
            from src.swarm.llm_multi import MultiProviderLLM
            self._team = MultiProviderLLM()
            if self._jurisdiction:
                self._team.apply_jurisdiction(
                    exec_locus_pin=self._jurisdiction.get("exec_locus", "any"),
                    cloud_shift_active=self._jurisdiction.get("cloud_shift_active", True),
                )
        return self._team

    async def call(self, system: str, user: str) -> str:
        if LLM_PROVIDER == "stub":
            await asyncio.sleep(0.02)
            return (
                "RESULT: Stub output. Wire LLM_PROVIDER.\n"
                "ARTIFACTS: \n"
                "CHECKS: \n"
                "RISKS: rate limits; missing repo context\n"
                "CAPABILITIES: git_status {}\n"
                "MODE: ANALYTIC\n"
                "CLAIM_LEDGER:\n"
                "  - claim_id: CLM-001\n"
                "    label: hypothesis\n"
                "    confidence: 0.5\n"
                "    text: \"Stub claim pending real LLM integration\"\n"
                'COMPRESSED_HANDOFF: {"goal":"","facts":[],"decisions":[],"tasks":[],"risks":[],"next":[]}'
            )

        team = self._get_team()
        if team is None:
            raise RuntimeError("LLM_PROVIDER is set but no API keys are configured.")

        # Role hints are resolved from env_1 (per-provider role_hint) inside the
        # swarm; no vendor names are referenced here.
        out = await team.call_team(system, user)
        return out["merged"]


class _StubProviderReply:
    ok = True


class WaveOrchestrator:
    def __init__(self, broker: CapabilityBroker):
        self.broker = broker
        self.llm = LLM()
        self.sem = asyncio.Semaphore(WAVE_CONCURRENCY)
        self.hr_monitor = HallucinationMonitor()
        self.intent = IntentBinding(
            objective_id="GOAL-001",
            success_criteria=["Complete user goal"],
            active_constraints=["No raw secrets", "Policy gates enforced"],
        )

        try:
            self.provenance = ProvenanceTree() if ProvenanceTree else None
        except Exception as e:
            logger.warning("ProvenanceTree init failed: %s", e)
            self.provenance = None

        try:
            self.consistency = ConsistencyChecker() if ConsistencyChecker else None
        except Exception as e:
            logger.warning("ConsistencyChecker init failed: %s", e)
            self.consistency = None

        try:
            self.auditor = ReflexiveAuditor() if ReflexiveAuditor else None
        except Exception as e:
            logger.warning("ReflexiveAuditor init failed: %s", e)
            self.auditor = None

        try:
            self.sentinel = GovernanceSentinel() if GovernanceSentinel else None
        except Exception as e:
            logger.warning("GovernanceSentinel init failed: %s", e)
            self.sentinel = None

        try:
            self.red_team = RedTeamAgent() if RedTeamAgent else None
        except Exception as e:
            logger.warning("RedTeamAgent init failed: %s", e)
            self.red_team = None

    async def run(self, goal: str, domain: str = "general", team: str = "default", task_run_id: Optional[int] = None) -> Dict[str, Any]:
        # Jurisdiction routing: resolve the exec_locus / cloud_shift decision once
        # before dispatching any provider waves, then pin the provider swarm to it.
        jurisdiction = {}
        if jurisdiction_metadata is not None:
            try:
                jurisdiction = jurisdiction_metadata(domain=domain, team=team)
                self.llm.apply_jurisdiction(jurisdiction)
            except Exception as e:
                logger.warning("Jurisdiction metadata resolution failed: %s", e)
        self.jurisdiction = jurisdiction

        handoff = Handoff(
            goal=goal.strip(),
            facts=[
                "Runtime: Telegram bot on Replit (polling).",
                "Guardrails: brokered capabilities; policy gates; no raw secrets.",
                "Target: multi-project swarm; git + terraform + asana + 1Password patterns.",
            ],
            decisions=[
                "Use Porter waves + compression to prevent token blowup.",
                "Bound concurrency; simulate 100+ agents via queued microtasks.",
            ],
            tasks=[
                "Wave 0: route + clarify + enumerate impacted repos/environments",
                "Wave 1: architecture + task graph",
                "Wave 2: code diffs + tests + PR plan",
                "Wave 3: verify + terraform plan + staged deploy",
            ],
            risks=[],
            next=[],
        )

        # Load enacted governance settings once at run-start so policy decisions
        # made during the run reflect any proposals approved since last boot.
        try:
            from src.swarm.governance_settings import GovernanceSettings
            gov = GovernanceSettings.load()
        except Exception as e:
            logger.warning("Failed to load governance settings: %s", e)
            from src.swarm.governance_settings import GovernanceSettings
            gov = GovernanceSettings()

        all_caps: List[Tuple[str, Dict[str, Any], str]] = []
        all_compliance_scores: List[Dict[str, Any]] = []
        all_dissent: List[str] = []
        aborted = False

        # Extra debate rounds: appended as additional wave iterations when enacted
        total_waves = MAX_WAVES + gov.extra_debate_rounds

        for w in range(total_waves):
            # If skeptic override is enacted, inject a dedicated SKEPTIC job into
            # the first wave's job list by setting a transient flag on the instance.
            if w == 0 and gov.enable_skeptic_override:
                self._skeptic_override_active = True
            else:
                self._skeptic_override_active = False

            wave_results = await self._run_wave(w, handoff)

            try:
                for r in wave_results:
                    try:
                        report = analyze_agent_output(
                            r.result,
                            intent_refs=[self.intent.objective_id],
                            mode=r.cognitive_role if r.cognitive_role else "ANALYTIC",
                        )
                        r.compliance_score = report.score

                        features = build_features_from_compliance(
                            report,
                            provider_replies=[_StubProviderReply()],
                            constraint_budget_ratio=report.metrics.constraint_violations / max(1, 5),
                        )
                        hr = compute_hallucination_risk(features)
                        r.hallucination_risk = hr

                        control = get_control_action(hr)
                        self.hr_monitor.record(hr=hr, wave=w, agent=r.agent)

                        # Enacted policy: if the agent's CS falls below the
                        # governance-enacted min_cs_threshold, log a degradation note.
                        cs_threshold = gov.min_cs_threshold
                        cs_ok = report.score >= cs_threshold
                        all_compliance_scores.append({
                            "agent": r.agent,
                            "score": report.score,
                            "tier": report.tier.value,
                            "hr": round(hr, 3),
                            "control": control.description[:100],
                            "below_cs_threshold": not cs_ok,
                            "cs_threshold": cs_threshold,
                        })
                        if not cs_ok:
                            logger.warning(
                                "Agent %s CS=%d is below enacted threshold %d (wave %d)",
                                r.agent, report.score, cs_threshold, w,
                            )
                        try:
                            if self.provenance is not None:
                                self.provenance.add_provenance(
                                    claim_id=f"W{w}-{r.agent}",
                                    provider=r.agent.split("-")[-1] if "-" in r.agent else "unknown",
                                    role=r.cognitive_role or "unknown",
                                    wave=w,
                                    evidence=[],
                                    parent_ids=[],
                                    hr=r.hallucination_risk,
                                    cs=r.compliance_score,
                                )
                        except Exception as e:
                            logger.warning("Provenance tracking failed: %s", e)

                    except Exception as e:
                        logger.warning("Cognitive scoring failed for %s: %s", r.agent, e)

                if self.hr_monitor.should_abort():
                    logger.warning("Hallucination monitor triggered abort after wave %d", w)
                    aborted = True
                    break

                # Enacted policy: abort immediately when halt_on_critical is set
                # and the sentinel has raised unacknowledged critical alerts.
                if gov.halt_on_critical and self.sentinel is not None:
                    if self.sentinel.should_escalate():
                        logger.warning(
                            "GovernanceSentinel triggered halt_on_critical abort after wave %d", w
                        )
                        aborted = True
                        break
            except Exception as e:
                logger.warning("Wave %d cognitive scoring sweep failed: %s", w, e)

            handoff = self._merge(handoff, wave_results)

            try:
                if self.auditor is not None:
                    cs_list = [r.compliance_score for r in wave_results]
                    self.auditor.audit_wave(
                        wave_idx=w,
                        agent_results=[{"agent": r.agent, "result": r.result[:200], "cs": r.compliance_score, "hr": r.hallucination_risk, "role": r.cognitive_role} for r in wave_results],
                        compliance_scores=cs_list,
                        dissent_log=handoff.dissent,
                    )
            except Exception as e:
                logger.warning("Reflexive auditor failed for wave %d: %s", w, e)

            try:
                if self.sentinel is not None:
                    self.sentinel.evaluate(
                        wave_idx=w,
                        compliance_scores=[r.compliance_score for r in wave_results],
                        hallucination_risks=[r.hallucination_risk for r in wave_results],
                        dissent_count=len(handoff.dissent),
                        provider_statuses={},
                    )
                    if self.sentinel.should_escalate():
                        logger.warning("Sentinel ESCALATION triggered at wave %d", w)

                    # Enacted policy: quarantine providers flagged by sentinel when
                    # swarm.quarantine_flagged_providers is true.
                    if gov.quarantine_flagged_providers:
                        throttle_recs = self.sentinel.get_throttle_recommendations()
                        quarantine_list = [
                            p for p, rec in throttle_recs.items()
                            if rec == "quarantine"
                        ]
                        if quarantine_list:
                            try:
                                self.llm.quarantine_providers(quarantine_list)
                                logger.info(
                                    "GovernanceSettings quarantined providers: %s", quarantine_list
                                )
                            except Exception as qe:
                                logger.warning("Provider quarantine failed: %s", qe)
            except Exception as e:
                logger.warning("Sentinel evaluation failed: %s", w, e)

            for r in wave_results:
                for cap, params in r.capability_requests:
                    br = await self.broker.request(cap, params)
                    all_caps.append((cap, params, (br.output or br.decision.reason)))

                    if br.output:
                        handoff.facts.append(f"[cap:{cap}] {br.output[:200]}")

            all_dissent.extend(handoff.dissent)

        avg_hr = self.hr_monitor.get_average()
        trend = self.hr_monitor.get_trend()
        from src.swarm.hallucination import get_risk_tier
        tier = get_risk_tier(avg_hr)

        # Record the exec_locus / fallback tier actually used for this run. When a
        # live provider team exists, ask it for the effective routing after the
        # jurisdiction filter; otherwise fall back to the resolved metadata.
        jur = dict(self.jurisdiction or {})
        try:
            team = self.llm._get_team()
            if team is not None:
                eff_tier, eff_locus, eff_outcome = team.effective_routing()
                jur["fallback_tier"] = eff_tier
                jur["exec_locus"] = eff_locus
                jur["outcome"] = eff_outcome
        except Exception as e:
            logger.warning("Effective routing resolution failed: %s", e)

        final = {
            "GOAL": handoff.goal,
            "FACTS": clamp_list(handoff.facts, 10),
            "DECISIONS": clamp_list(handoff.decisions, 10),
            "NEXT_TASKS": clamp_list(handoff.tasks, 12),
            "RISKS": clamp_list(handoff.risks, 6),
            "NEXT_NEEDS": clamp_list(handoff.next, 10),
            "CAPABILITY_LOG": [
                {"capability": c, "params": p, "output": o[:300]}
                for (c, p, o) in all_caps[:25]
            ],
            "NOTE": (
                "This is the governed swarm scaffold. To unlock real coding power: "
                "wire LLM.call() to your provider, and add repo grounding (read files, run tests) "
                "plus CI-based terraform plan/apply."
            ),
            "COGNITIVE_ARCHITECTURE": "Constitution v1.0 - Epistemic Governance Active",
            "COMPLIANCE_SCORES": all_compliance_scores,
            "HALLUCINATION_RISK": {
                "average": round(avg_hr, 3),
                "trend": trend,
                "tier": tier.value,
            },
            "DISSENT_LOG": all_dissent,
            "CLAIM_LEDGER": handoff.claim_ledger_summary or "No claims recorded",
            "PROVENANCE_SUMMARY": self.provenance.to_summary() if self.provenance else "N/A",
            "AUDITOR_STATUS": self.auditor.get_status() if self.auditor else {},
            "SENTINEL_ALERTS": [{"type": a.alert_type, "severity": a.severity, "desc": a.description[:200]} for a in (self.sentinel.get_active_alerts() if self.sentinel else [])],
            "META_CLAIMS": [{"category": mc.category, "severity": mc.severity, "desc": mc.description[:150]} for mc in (self.auditor.get_meta_claims() if self.auditor else [])][:10],
            "JURISDICTION": {
                "domain": jur.get("domain", "general"),
                "team": jur.get("team", "default"),
                "exec_locus": jur.get("exec_locus"),
                "fallback_tier": jur.get("fallback_tier"),
                "cloud_shift_active": jur.get("cloud_shift_active"),
                "outcome": jur.get("outcome"),
                "source": jur.get("jurisdiction_source"),
            },
        }

        if aborted:
            final["ABORT_REASON"] = (
                "Hallucination monitor detected 3+ consecutive CRITICAL risk scores. "
                "Pipeline halted to prevent unreliable outputs."
            )

        self._index_run(goal, domain, final, aborted, all_compliance_scores, all_dissent, task_run_id=task_run_id)

        return final

    def _index_run(
        self,
        goal: str,
        domain: str,
        final: Dict[str, Any],
        aborted: bool,
        compliance_scores: List[Dict],
        dissent: List,
        task_run_id: Optional[int] = None,
    ) -> None:
        """Write a compact, searchable SwarmRunIndex record for this run.

        Called after every completed run so the /runs/search view can index
        across goals, epistemic categories, provenance lineage, and dissent.
        Failures are silently suppressed — a DB outage must never crash the swarm.
        """
        try:
            import uuid as _uuid
            import json as _json
            from src.database import SessionLocal
            from src.models import SwarmRunIndex

            run_id = str(_uuid.uuid4())

            synthesis = (
                final.get("FACTS", [""])[0][:500]
                if final.get("FACTS") else
                (final.get("CLAIM_LEDGER") or "")[:500]
            )

            # Searchable claim text: extract all claim entries from the ledger
            claim_text = (final.get("CLAIM_LEDGER") or "")[:4000]
            # Augment with any fact entries from the final handoff
            if final.get("FACTS"):
                claim_text = claim_text + "\n" + "\n".join(str(f) for f in final.get("FACTS", [])[:20])

            # Searchable dissent descriptors: role + reason + risk level per entry
            dissent_entries = []
            for entry in (final.get("DISSENT_LOG") or [])[:30]:
                dissent_entries.append(str(entry)[:200])
            dissent_json_str = _json.dumps(dissent_entries) if dissent_entries else "[]"

            avg_cs = None
            if compliance_scores:
                scores = [c.get("score", 0) for c in compliance_scores if isinstance(c.get("score"), (int, float))]
                if scores:
                    avg_cs = sum(scores) / len(scores)

            avg_hr = None
            hr_data = final.get("HALLUCINATION_RISK", {})
            if isinstance(hr_data, dict) and hr_data.get("average") is not None:
                avg_hr = hr_data["average"]

            epistemic_summary = {}
            claim_ledger_text = final.get("CLAIM_LEDGER", "")
            if claim_ledger_text and isinstance(claim_ledger_text, str):
                for label in ("verified", "derived", "hypothesis", "unknown"):
                    epistemic_summary[label] = claim_ledger_text.lower().count(label)

            provenance_summary = final.get("PROVENANCE_SUMMARY", "")

            meta_claims = final.get("META_CLAIMS", [])

            db = SessionLocal()
            try:
                record = SwarmRunIndex(
                    run_id=run_id,
                    goal=goal[:2000],
                    domain=(domain or "general")[:100],
                    synthesis=synthesis,
                    epistemic_summary_json=_json.dumps(epistemic_summary),
                    provenance_lineage_json=(provenance_summary[:2000] if provenance_summary else ""),
                    claim_text=claim_text[:5000] if claim_text else "",
                    dissent_json=dissent_json_str,
                    dissent_count=len(dissent),
                    aborted=aborted,
                    avg_cs=round(avg_cs, 2) if avg_cs is not None else None,
                    avg_hr=round(avg_hr, 4) if avg_hr is not None else None,
                    meta_claims_count=len(meta_claims),
                    task_run_id=task_run_id,
                )
                db.add(record)
                db.commit()
                logger.info("Run indexed: %s (goal=%s)", run_id, goal[:60])
            except Exception as e:
                logger.warning("Failed to persist run index: %s", e)
                db.rollback()
            finally:
                db.close()
        except Exception as e:
            logger.warning("_index_run failed: %s", e)

    async def _run_wave(self, wave_idx: int, handoff: Handoff) -> List[AgentResult]:
        jobs = self._build_jobs(wave_idx, handoff.goal)
        results: List[AgentResult] = []

        async def run_one(i: int, job: Dict[str, str]):
            async with self.sem:
                user_prompt = self._worker_prompt(wave_idx, i, job, handoff)
                text = await self.llm.call(WORKER_SYSTEM_PROMPT, user_prompt)
                parsed = self._parse_worker(text)
                updated = replace(
                    parsed,
                    agent=f"W{wave_idx}-A{i}-{job['role']}",
                    wave=wave_idx,
                    cognitive_role=job.get("cognitive_role_name", ""),
                )
                results.append(updated)

        await asyncio.gather(*(run_one(i, j) for i, j in enumerate(jobs)))
        return results

    def _build_jobs(self, wave_idx: int, goal: str) -> List[Dict[str, str]]:
        if wave_idx == 0:
            roles = ["Router", "Requirements Analyst", "Repo Scout", "Asana Planner", "Risk Analyst"]
        elif wave_idx == 1:
            roles = ["Architecture Lead", "API Designer", "Data Modeler", "DevOps Planner", "Security Reviewer"]
        elif wave_idx == 2:
            roles = ["Backend Builder", "Infra Builder", "Test Engineer", "Refactor Specialist", "PR Curator"]
        else:
            roles = [
                "Release Captain",
                "SRE Reviewer",
                "Terraform Planner",
                "Cost Engineer",
                "Monetization Strategist",
            ]

        try:
            cognitive_roles = map_wave_to_cognitive_roles(wave_idx)
        except Exception as e:
            logger.warning("Failed to map cognitive roles for wave %d: %s", wave_idx, e)
            cognitive_roles = [CognitiveRole.ARCHITECT]

        jobs = []
        for i in range(AGENTS_PER_WAVE):
            role = roles[i % len(roles)]
            cog_role = cognitive_roles[i % len(cognitive_roles)]

            try:
                role_suffix = get_role_prompt_suffix(cog_role)
            except Exception:
                role_suffix = ""

            task_desc = (
                f"[Wave {wave_idx}] As {role}, produce mergeable outputs for: {goal}. "
                f"Request broker capabilities rather than secrets. Provide checks and compressed handoff."
            )
            if role_suffix:
                task_desc += f"\nCOGNITIVE DIRECTIVE: {role_suffix}"

            jobs.append(
                {
                    "role": role,
                    "task": task_desc,
                    "cognitive_role_name": cog_role.value,
                }
            )
        return jobs

    def _worker_prompt(self, wave: int, idx: int, job: Dict[str, str], handoff: Handoff) -> str:
        cog_role_name = job.get("cognitive_role_name", "")
        is_creative = cog_role_name in ("creative_divergence", "exploratory")
        mode = "EXPLORATORY" if is_creative else "ANALYTIC"

        try:
            role_suffix = get_role_prompt_suffix(CognitiveRole(cog_role_name)) if cog_role_name else ""
        except (ValueError, Exception):
            role_suffix = ""

        prompt = (
            f"GOAL: {handoff.goal}\n"
            f"WAVE: {wave}\n"
            f"AGENT_INDEX: {idx}\n"
            f"FACTS: {handoff.facts}\n"
            f"DECISIONS: {handoff.decisions}\n"
            f"TASK: {job['task']}\n"
            f"CONSTRAINTS: No raw secrets; request capabilities; small diffs; clear checks.\n"
            f"HANDOFF_SCHEMA: goal,facts,decisions,tasks,risks,next\n"
            f"\n"
            f"CONSTITUTION:\n{CONSTITUTION_RULES}\n"
            f"\n"
            f"INTENT_BINDING:\n"
            f"  objective_id: {self.intent.objective_id}\n"
            f"  constraints: {self.intent.active_constraints}\n"
            f"\n"
            f"MODE: {mode}\n"
        )

        if role_suffix:
            prompt += f"\nCOGNITIVE_ROLE_FOCUS: {role_suffix}\n"

        return prompt

    def _parse_worker(self, text: str) -> AgentResult:
        def grab(label: str) -> str:
            m = re.search(rf"{label}\s*:\s*(.*)", text)
            return m.group(1).strip() if m else ""

        result = grab("RESULT") or text[:300]
        artifacts = [x.strip() for x in grab("ARTIFACTS").split(";") if x.strip()] if grab("ARTIFACTS") else []
        checks = [x.strip() for x in grab("CHECKS").split(";") if x.strip()] if grab("CHECKS") else []
        risks = [x.strip() for x in grab("RISKS").split(";") if x.strip()] if grab("RISKS") else []
        handoff_json = grab("COMPRESSED_HANDOFF") or "{}"

        capline = grab("CAPABILITIES")
        caps: List[Tuple[str, Dict[str, Any]]] = []
        if capline:
            parts = [p.strip() for p in capline.split(";") if p.strip()]
            for p in parts:
                try:
                    cap, j = p.split(" ", 1)
                    params = json.loads(j.strip())
                    caps.append((cap.strip(), params))
                except Exception:
                    pass

        mode_str = grab("MODE") or "ANALYTIC"

        compliance_score = 100
        hr_risk = 0.0

        try:
            ledger_match = re.search(r"CLAIM_LEDGER\s*:(.*?)(?=\n[A-Z_]+\s*:|$)", text, re.DOTALL)
            if ledger_match:
                ClaimLedger.from_text(ledger_match.group(1))
        except Exception as e:
            logger.warning("Claim ledger parse failed: %s", e)

        try:
            violations = validate_language(text)
            if violations:
                logger.info("Language violations: %s", violations)
        except Exception as e:
            logger.warning("Language validation failed: %s", e)

        try:
            report = analyze_agent_output(
                text,
                intent_refs=["GOAL-001"],
                mode=mode_str,
            )
            compliance_score = report.score

            features = build_features_from_compliance(
                report,
                provider_replies=[_StubProviderReply()],
                constraint_budget_ratio=report.metrics.constraint_violations / max(1, 5),
            )
            hr_risk = compute_hallucination_risk(features)
        except Exception as e:
            logger.warning("Compliance scoring in parse failed: %s", e)

        return AgentResult(
            agent="AGENT",
            wave=0,
            result=result,
            artifacts=artifacts,
            checks=checks,
            risks=risks,
            handoff_json=handoff_json,
            capability_requests=caps,
            compliance_score=compliance_score,
            hallucination_risk=hr_risk,
            cognitive_role=mode_str,
        )

    def _merge(self, prev: Handoff, results: List[AgentResult]) -> Handoff:
        facts = prev.facts[:]
        decisions = prev.decisions[:]
        tasks = prev.tasks[:]
        risks = prev.risks[:]
        nxt = prev.next[:]
        dissent = prev.dissent[:]
        ledger_parts: List[str] = []

        for r in results[:25]:
            if r.result and len(facts) < 25:
                facts.append(f"{r.agent}: {r.result[:140]}")
            for a in r.artifacts[:2]:
                if len(tasks) < 35:
                    tasks.append(f"{r.agent} artifact: {a[:180]}")
            for c in r.checks[:2]:
                if len(decisions) < 20:
                    decisions.append(f"{r.agent} check: {c[:180]}")
            for rr in r.risks[:1]:
                if len(risks) < 10:
                    risks.append(f"{r.agent} risk: {rr[:180]}")

        try:
            votes: Dict[str, Dict] = {}
            for r in results[:25]:
                base_weight = 1.0
                try:
                    cog_role = CognitiveRole(r.cognitive_role) if r.cognitive_role else CognitiveRole.ARCHITECT
                    base_weight = ROLE_BASE_WEIGHTS.get(cog_role, 1.0)
                except (ValueError, Exception):
                    pass

                w = compute_vote_weight(
                    base_role_weight=base_weight,
                    compliance_score=r.compliance_score,
                    evidence_strength=0.7,
                    hallucination_risk=r.hallucination_risk,
                )
                votes[r.agent] = {
                    "weight": w,
                    "accept": r.compliance_score >= 65,
                    "reason": f"score={r.compliance_score}, hr={r.hallucination_risk:.2f}",
                }

            consensus = run_consensus(votes)

            if consensus.dissent:
                for d in consensus.dissent:
                    dissent_str = f"Dissent from {', '.join(d.dissenters[:3])}: {d.reason[:200]} (risk={d.risk_level})"
                    dissent.append(dissent_str)
        except Exception as e:
            logger.warning("Consensus computation failed: %s", e)

        try:
            for r in results[:10]:
                ledger_match = re.search(r"CLAIM_LEDGER\s*:(.*?)(?=\n[A-Z_]+\s*:|$)", r.result, re.DOTALL)
                if ledger_match:
                    ledger = ClaimLedger.from_text(ledger_match.group(1))
                    load_bearing = ledger.get_load_bearing_claims()
                    for claim in load_bearing[:3]:
                        ledger_parts.append(f"[{claim.claim_id}] {claim.label.value} c={claim.confidence}: {claim.text[:100]}")
        except Exception as e:
            logger.warning("Claim ledger merge failed: %s", e)

        claim_ledger_summary = prev.claim_ledger_summary
        if ledger_parts:
            claim_ledger_summary = (claim_ledger_summary + "\n" if claim_ledger_summary else "") + "\n".join(ledger_parts)

        if not risks:
            risks = [
                "Parallel LLM calls can hit rate limits; keep bounded concurrency + backoff.",
                "Without repo grounding (file reads/tests), agents may drift from reality.",
                "Terraform apply must be gated by plan + policy + environment.",
            ]

        if not nxt:
            nxt = [
                "Wire LLM provider in LLM.call().",
                "Add repo grounding: read file tree + open key files + run tests.",
                "Move terraform to CI with remote state; broker triggers plan/apply pipelines.",
                "Wire 1Password Connect (broker only) and never return raw secrets to agents.",
                "Wire Asana ingestion -> task graph -> updates back via broker.",
            ]

        return Handoff(
            goal=prev.goal,
            facts=facts[:30],
            decisions=decisions[:22],
            tasks=tasks[:40],
            risks=risks[:10],
            next=nxt[:15],
            dissent=dissent[:20],
            claim_ledger_summary=claim_ledger_summary[:2000] if claim_ledger_summary else "",
        )
