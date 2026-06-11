"""Cogitation — single governed cognition entrypoint.

Every agent (Coach, Fleet BotRunner, Web API task runner, rule-based subsystems
invoking reasoning) flows through ``Cogitation.think()``.  It sequences the
per-turn stages as first-class, named phases, delegates to the existing
WaveOrchestrator / MemorySubstrate / promptopt runtime, and persists a
DecisionTrace for every governed turn.

Architecture: runtime layer (docs/swarm_layer_architecture.md).
Call direction: Cogitation → WaveOrchestrator → LLM/broker/governance.
Constraint: wraps, never reimplements WaveOrchestrator.

Per-turn stages (in order):
  1. INTENT_ROUTE       — classify intent, resolve domain/team, pin jurisdiction
  2. RECALL             — inject relevant memories from the substrate
  3. LOAD_PROMPT        — load current evolved prompt card (promptopt)
  4. GOVERNED_REASONING — WaveOrchestrator.run() (full wave + governance loop)
  5. KNOWLEDGE_WRITEBACK— persist handoff to memory substrate
  6. REFLECTION         — collect auditor/sentinel meta-summary
  7. RECORD_TRACE       — persist DecisionTrace to DB
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stage enum
# ---------------------------------------------------------------------------

class CogitationStage(str, Enum):
    INTENT_ROUTE = "intent_route"
    RECALL = "recall"
    LOAD_PROMPT = "load_prompt"
    GOVERNED_REASONING = "governed_reasoning"
    KNOWLEDGE_WRITEBACK = "knowledge_writeback"
    REFLECTION = "reflection"
    RECORD_TRACE = "record_trace"


# ---------------------------------------------------------------------------
# Input / output contracts
# ---------------------------------------------------------------------------

@dataclass
class CogitationContext:
    """Caller-supplied context for one governed turn.

    All callers (coach, fleet, api, subsystems) pass an instance of this
    dataclass.  Cogitation never inspects raw secrets — they stay in the vault
    and are injected by the provider layer.
    """
    source: str                           # "coach" | "fleet" | "api" | "subsystem:<name>"
    strict_private: bool = False          # Coach Private-Mode: local providers only
    domain: str = "general"
    team: str = "default"
    task_run_id: Optional[int] = None
    namespace: Optional[str] = None       # Memory namespace for fleet bots
    extra_facts: List[str] = field(default_factory=list)  # Caller-supplied context facts
    persona_prompt: Optional[str] = None  # Fleet bot / coach system persona


@dataclass
class StageRecord:
    """Instrumentation record for one per-turn stage."""
    stage: str
    status: str          # "ok" | "skipped" | "error"
    duration_ms: float
    detail: str = ""


@dataclass
class CogitationResult:
    """Result of one governed turn through the Cogitation entrypoint.

    Compatible with the old ``MultiProviderLLM.call_team`` dict interface
    via the convenience properties below so existing callers (coach, fleet)
    need minimal changes.
    """
    turn_id: str
    source: str
    outcome: str                           # "completed" | "aborted" | "error"
    stages: List[StageRecord]
    result: Dict[str, Any]                 # WaveOrchestrator output dict
    decision_trace_id: Optional[int] = None

    # ------------------------------------------------------------------
    # Convenience accessors for backward-compat callers
    # ------------------------------------------------------------------

    @property
    def merged(self) -> str:
        """Synthesized answer text — primary output for coach / API callers."""
        r = self.result or {}
        decisions = r.get("DECISIONS") or []
        if decisions:
            return "\n".join(str(d) for d in decisions[:5])
        facts = r.get("FACTS") or []
        if facts:
            return "\n".join(str(f) for f in facts[:5])
        return str(r.get("NOTE", ""))

    @property
    def compliance_scores(self) -> Dict[str, int]:
        cs_list = (self.result or {}).get("COMPLIANCE_SCORES") or []
        return {
            item["agent"]: item["score"]
            for item in cs_list
            if isinstance(item, dict) and "agent" in item and "score" in item
        }

    @property
    def hallucination_risks(self) -> Dict[str, Any]:
        return (self.result or {}).get("HALLUCINATION_RISK") or {}

    def as_call_team_dict(self) -> Dict[str, Any]:
        """Serialise to the old call_team() return shape for drop-in compat."""
        return {
            "merged": self.merged,
            "compliance_scores": self.compliance_scores,
            "hallucination_risks": self.hallucination_risks,
            "replies": [],
            "_cogitation_turn_id": self.turn_id,
            "_cogitation_trace_id": self.decision_trace_id,
        }


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

class Cogitation:
    """Single governed cognition entrypoint.

    One instance per turn (stateful stage list).  Thread-safe for concurrent
    ``asyncio.gather`` usage because each ``.think()`` call is independent.
    """

    def __init__(self) -> None:
        self._stages: List[StageRecord] = []
        self._t0: Dict[str, float] = {}

    # ------------------------------------------------------------------ #
    # Stage instrumentation helpers
    # ------------------------------------------------------------------ #

    def _begin(self, stage: CogitationStage) -> None:
        self._t0[stage.value] = time.time()

    def _end(self, stage: CogitationStage, status: str, detail: str = "") -> StageRecord:
        elapsed = round((time.time() - self._t0.pop(stage.value, time.time())) * 1000, 1)
        rec = StageRecord(stage=stage.value, status=status, duration_ms=elapsed,
                          detail=(detail or "")[:500])
        self._stages.append(rec)
        logger.debug("[COGITATION:%s] %s (%.0fms) — %s",
                     stage.value, status, elapsed, (detail or "")[:100])
        return rec

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def think(
        self,
        goal: str,
        context: Optional[CogitationContext] = None,
    ) -> CogitationResult:
        """Execute one governed turn through all 7 stages.

        Never raises — on error the outcome is "error" and result contains an
        "ERROR" key with the description (governance pillar #1: fail loud, not
        silent fallback).
        """
        ctx = context or CogitationContext(source="api")
        turn_id = str(uuid.uuid4())
        self._stages = []
        self._t0 = {}
        outcome = "completed"
        orch_result: Dict[str, Any] = {}
        trace_id: Optional[int] = None

        # ── 1. INTENT_ROUTE ─────────────────────────────────────────────
        self._begin(CogitationStage.INTENT_ROUTE)
        try:
            domain = ctx.domain or "general"
            team = ctx.team or "default"
            self._end(CogitationStage.INTENT_ROUTE, "ok",
                      f"source={ctx.source} domain={domain} team={team} "
                      f"strict_private={ctx.strict_private}")
        except Exception as exc:
            self._end(CogitationStage.INTENT_ROUTE, "error", str(exc))

        # ── 2. RECALL ────────────────────────────────────────────────────
        self._begin(CogitationStage.RECALL)
        recalled_facts: List[str] = list(ctx.extra_facts or [])
        try:
            from src.memory import get_substrate
            mem = get_substrate()
            ns_goal = f"{ctx.namespace}:{goal}" if ctx.namespace else goal
            fresh = mem.inject_for_task(ns_goal, top_k=5) or []
            recalled_facts = list(ctx.extra_facts or []) + fresh
            self._end(CogitationStage.RECALL, "ok",
                      f"recalled={len(fresh)} fresh + {len(ctx.extra_facts)} context facts")
        except Exception as exc:
            self._end(CogitationStage.RECALL, "error", f"Memory recall failed: {exc}")

        # ── 3. LOAD_PROMPT ───────────────────────────────────────────────
        self._begin(CogitationStage.LOAD_PROMPT)
        try:
            from src.promptopt import store
            from src.promptopt.specs import JUDGE_PROGRAM
            ver_id, _instr, _ = store.get_live_instructions(JUDGE_PROGRAM)
            self._end(CogitationStage.LOAD_PROMPT, "ok",
                      f"program={JUDGE_PROGRAM} ver_id={ver_id}")
        except Exception as exc:
            self._end(CogitationStage.LOAD_PROMPT, "skipped",
                      f"promptopt unavailable: {exc}")

        # ── 4. GOVERNED_REASONING ────────────────────────────────────────
        self._begin(CogitationStage.GOVERNED_REASONING)
        try:
            augmented = self._build_augmented_goal(goal, ctx, recalled_facts)
            orch_result = await self._run_orchestrator(augmented, ctx)
            aborted = bool(orch_result.get("ABORT_REASON"))
            if aborted:
                outcome = "aborted"
            self._end(CogitationStage.GOVERNED_REASONING,
                      "aborted" if aborted else "ok",
                      f"aborted={aborted}")
        except Exception as exc:
            logger.error("[COGITATION] governed_reasoning failed for source=%s: %s",
                         ctx.source, exc)
            outcome = "error"
            orch_result = {"ERROR": str(exc)}
            self._end(CogitationStage.GOVERNED_REASONING, "error", str(exc))

        # ── 5. KNOWLEDGE_WRITEBACK ───────────────────────────────────────
        self._begin(CogitationStage.KNOWLEDGE_WRITEBACK)
        if outcome != "error":
            try:
                from src.memory import get_substrate
                mem = get_substrate()
                ns_goal = f"{ctx.namespace}:{goal}" if ctx.namespace else goal
                mem.persist_handoff(
                    goal=ns_goal,
                    facts=list(orch_result.get("FACTS") or [])[:10],
                    decisions=list(orch_result.get("DECISIONS") or [])[:8],
                    claim_ledger_summary=str(orch_result.get("CLAIM_LEDGER") or ""),
                    session_id=turn_id,
                )
                self._end(CogitationStage.KNOWLEDGE_WRITEBACK, "ok",
                          "facts+decisions persisted to substrate")
            except Exception as exc:
                self._end(CogitationStage.KNOWLEDGE_WRITEBACK, "error", str(exc))
        else:
            self._end(CogitationStage.KNOWLEDGE_WRITEBACK, "skipped",
                      "skipped — reasoning stage errored")

        # ── 6. REFLECTION ────────────────────────────────────────────────
        self._begin(CogitationStage.REFLECTION)
        try:
            auditor = orch_result.get("AUDITOR_STATUS") or {}
            sentinel = orch_result.get("SENTINEL_ALERTS") or []
            jur = orch_result.get("JURISDICTION") or {}
            self._end(CogitationStage.REFLECTION, "ok",
                      f"meta_claims={auditor.get('total_meta_claims', 0)} "
                      f"sentinel_alerts={len(sentinel)} "
                      f"exec_locus={jur.get('exec_locus', '?')}")
        except Exception as exc:
            self._end(CogitationStage.REFLECTION, "error", str(exc))

        # ── 7. RECORD_TRACE ──────────────────────────────────────────────
        self._begin(CogitationStage.RECORD_TRACE)
        try:
            trace_id = self._persist_trace(turn_id, ctx, goal, orch_result, outcome)
            self._end(CogitationStage.RECORD_TRACE, "ok", f"trace_id={trace_id}")
        except Exception as exc:
            self._end(CogitationStage.RECORD_TRACE, "error", str(exc))

        return CogitationResult(
            turn_id=turn_id,
            source=ctx.source,
            outcome=outcome,
            stages=list(self._stages),
            result=orch_result,
            decision_trace_id=trace_id,
        )

    def think_sync(
        self,
        goal: str,
        context: Optional[CogitationContext] = None,
    ) -> CogitationResult:
        """Synchronous wrapper for worker threads (coach, synchronous subsystems).

        MUST be called from a thread WITHOUT a running event loop (i.e., a
        plain worker thread, never from an async context — use ``await think()``
        there instead).
        """
        return asyncio.run(self.think(goal, context))

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_augmented_goal(
        goal: str,
        ctx: CogitationContext,
        recalled_facts: List[str],
    ) -> str:
        parts: List[str] = []
        if ctx.persona_prompt:
            parts.append(ctx.persona_prompt.strip())
        parts.append(f"GOAL:\n{goal.strip()}")
        if recalled_facts:
            parts.append("RECALLED MEMORY:\n" + "\n".join(str(f) for f in recalled_facts[:10]))
        return "\n\n".join(parts)

    @staticmethod
    async def _run_orchestrator(
        augmented_goal: str,
        ctx: CogitationContext,
    ) -> Dict[str, Any]:
        """Instantiate a fresh WaveOrchestrator and run the governed loop.

        Wraps, never reimplements, WaveOrchestrator.
        Jurisdiction (strict_private / exec_locus) is applied before the run.
        """
        from src.swarm.broker import CapabilityBroker
        from src.swarm.policy import PolicyEngine
        from src.swarm.integrations import Integrations
        from src.swarm.orchestrator import WaveOrchestrator

        policy = PolicyEngine()
        broker = CapabilityBroker(policy, Integrations())
        orch = WaveOrchestrator(broker)

        if ctx.strict_private:
            orch.llm.apply_jurisdiction({
                "cloud_shift_active": False,
                "exec_locus": "local",
            })

        return await orch.run(
            goal=augmented_goal,
            domain=ctx.domain or "general",
            team=ctx.team or "default",
            task_run_id=ctx.task_run_id,
        )

    def _persist_trace(
        self,
        turn_id: str,
        ctx: CogitationContext,
        goal: str,
        orch_result: Dict[str, Any],
        outcome: str,
    ) -> Optional[int]:
        """Write a DecisionTrace row to the DB.  Returns the row id or None."""
        import json as _json
        try:
            from src.database import SessionLocal
            from src.models import DecisionTrace

            hr_info = orch_result.get("HALLUCINATION_RISK") or {}
            cs_list = orch_result.get("COMPLIANCE_SCORES") or []
            avg_cs: Optional[float] = None
            if cs_list:
                scores = [s.get("score", 0) for s in cs_list if isinstance(s, dict)]
                if scores:
                    avg_cs = round(sum(scores) / len(scores), 2)

            stages_data = [
                {
                    "stage": s.stage,
                    "status": s.status,
                    "duration_ms": s.duration_ms,
                    "detail": s.detail,
                }
                for s in self._stages
            ]

            trace = DecisionTrace(
                turn_id=turn_id,
                source=ctx.source,
                goal_preview=(orch_result.get("GOAL") or goal)[:500],
                stages_json=_json.dumps(stages_data, default=str),
                jurisdiction_json=_json.dumps(
                    orch_result.get("JURISDICTION") or {}, default=str
                ),
                avg_cs=avg_cs,
                avg_hr=hr_info.get("average"),
                hr_tier=hr_info.get("tier"),
                provenance_summary=str(
                    orch_result.get("PROVENANCE_SUMMARY") or ""
                )[:2000],
                tool_calls_json=_json.dumps(
                    orch_result.get("CAPABILITY_LOG") or [], default=str
                ),
                outcome=outcome,
                aborted=(outcome == "aborted"),
                task_run_id=ctx.task_run_id,
            )
            db = SessionLocal()
            try:
                db.add(trace)
                db.commit()
                db.refresh(trace)
                return trace.id
            finally:
                db.close()
        except Exception as exc:
            logger.warning("[COGITATION] DecisionTrace persist failed: %s", exc)
            return None
