import os
import asyncio
import json
import re
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Tuple

from src.swarm.prompts import PORTER_SYSTEM_PROMPT, WORKER_SYSTEM_PROMPT
from src.swarm.utils import safe_json_dumps, clamp_list
from src.swarm.broker import CapabilityBroker

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


class LLM:
    async def call(self, system: str, user: str) -> str:
        if LLM_PROVIDER == "stub":
            await asyncio.sleep(0.02)
            return (
                "RESULT: Stub output. Wire LLM_PROVIDER.\n"
                "ARTIFACTS: \n"
                "CHECKS: \n"
                "RISKS: rate limits; missing repo context\n"
                "CAPABILITIES: git_status {}\n"
                'COMPRESSED_HANDOFF: {"goal":"","facts":[],"decisions":[],"tasks":[],"risks":[],"next":[]}'
            )
        raise RuntimeError(
            "LLM_PROVIDER is not implemented. Set LLM_PROVIDER=stub or implement provider in orchestrator.py."
        )


class WaveOrchestrator:
    def __init__(self, broker: CapabilityBroker):
        self.broker = broker
        self.llm = LLM()
        self.sem = asyncio.Semaphore(WAVE_CONCURRENCY)

    async def run(self, goal: str) -> Dict[str, Any]:
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

        all_caps: List[Tuple[str, Dict[str, Any], str]] = []

        for w in range(MAX_WAVES):
            wave_results = await self._run_wave(w, handoff)
            handoff = self._merge(handoff, wave_results)

            for r in wave_results:
                for cap, params in r.capability_requests:
                    br = await self.broker.request(cap, params)
                    all_caps.append((cap, params, (br.output or br.decision.reason)))

                    if br.output:
                        handoff.facts.append(f"[cap:{cap}] {br.output[:200]}")

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
        }
        return final

    async def _run_wave(self, wave_idx: int, handoff: Handoff) -> List[AgentResult]:
        jobs = self._build_jobs(wave_idx, handoff.goal)
        results: List[AgentResult] = []

        async def run_one(i: int, job: Dict[str, str]):
            async with self.sem:
                user_prompt = self._worker_prompt(wave_idx, i, job, handoff)
                text = await self.llm.call(WORKER_SYSTEM_PROMPT, user_prompt)
                parsed = self._parse_worker(text)
                updated = replace(parsed, agent=f"W{wave_idx}-A{i}-{job['role']}", wave=wave_idx)
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

        jobs = []
        for i in range(AGENTS_PER_WAVE):
            role = roles[i % len(roles)]
            jobs.append(
                {
                    "role": role,
                    "task": (
                        f"[Wave {wave_idx}] As {role}, produce mergeable outputs for: {goal}. "
                        f"Request broker capabilities rather than secrets. Provide checks and compressed handoff."
                    ),
                }
            )
        return jobs

    def _worker_prompt(self, wave: int, idx: int, job: Dict[str, str], handoff: Handoff) -> str:
        return (
            f"GOAL: {handoff.goal}\n"
            f"WAVE: {wave}\n"
            f"AGENT_INDEX: {idx}\n"
            f"FACTS: {handoff.facts}\n"
            f"DECISIONS: {handoff.decisions}\n"
            f"TASK: {job['task']}\n"
            f"CONSTRAINTS: No raw secrets; request capabilities; small diffs; clear checks.\n"
            f"HANDOFF_SCHEMA: goal,facts,decisions,tasks,risks,next\n"
        )

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

        return AgentResult(
            agent="AGENT",
            wave=0,
            result=result,
            artifacts=artifacts,
            checks=checks,
            risks=risks,
            handoff_json=handoff_json,
            capability_requests=caps,
        )

    def _merge(self, prev: Handoff, results: List[AgentResult]) -> Handoff:
        facts = prev.facts[:]
        decisions = prev.decisions[:]
        tasks = prev.tasks[:]
        risks = prev.risks[:]
        nxt = prev.next[:]

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
        )
