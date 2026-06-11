"""CognitionHook — lightweight governed cognition envelope for rule-based subsystems.

Finance, browser, subscriptions, vendors, and avatar are primarily deterministic
(rule-based, provider-API, session-mgmt).  This hook gives them a consistent
governance pass and DecisionTrace record at their reasoning-adjacent decision
points WITHOUT injecting LLM calls into purely deterministic paths.

Usage pattern (fire-and-forget, synchronous):

    from src.swarm.cognition_hook import CognitionHook

    hook = CognitionHook("finance")
    hook.record_event(
        intent="attribute_transactions",
        outcome="ok",
        detail="Attributed 42 transactions via rule-based vendor matching",
        governance_output={"vendor_count": 42, "unattributed": 3},
    )

For reasoning-adjacent paths where the subsystem DOES invoke an LLM, pass
``invoke_reasoning=True`` and the hook will route the call through the full
Cogitation entrypoint and return a governed result.

Architecture: runtime-adjacent helper; never imports from bridge or seam.
"""
from __future__ import annotations

import logging
import uuid
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class CognitionHook:
    """Governed cognition envelope for rule-based subsystems.

    Each subsystem instance (finance, browser, avatar, …) creates one hook at
    init time and calls ``record_event()`` at decision points.  When the
    subsystem needs governed LLM inference, ``invoke_reasoning()`` routes the
    call through the full Cogitation entrypoint.
    """

    def __init__(self, subsystem: str) -> None:
        self._subsystem = subsystem
        self._source = f"subsystem:{subsystem}"

    # ------------------------------------------------------------------ #
    # Deterministic event recording (no LLM call)
    # ------------------------------------------------------------------ #

    def record_event(
        self,
        intent: str,
        outcome: str,
        detail: str = "",
        governance_output: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """Persist a minimal DecisionTrace for a deterministic subsystem decision.

        Records the governed envelope (intent, outcome, governance metadata)
        without any LLM call.  Returns the trace id or None on DB failure.
        """
        turn_id = str(uuid.uuid4())
        t0 = time.time()
        stage = {
            "stage": "deterministic_decision",
            "status": outcome or "ok",
            "duration_ms": 0.0,
            "detail": (detail or "")[:500],
        }
        return self._write_trace(
            turn_id=turn_id,
            goal_preview=f"[{self._subsystem}] {intent}"[:500],
            stages=[stage],
            jurisdiction={},
            avg_cs=None,
            avg_hr=None,
            hr_tier=None,
            provenance_summary="",
            tool_calls=[],
            outcome=outcome or "ok",
            aborted=False,
            task_run_id=None,
        )

    # ------------------------------------------------------------------ #
    # Governed reasoning path (full Cogitation entrypoint)
    # ------------------------------------------------------------------ #

    def invoke_reasoning(
        self,
        goal: str,
        domain: str = "general",
        team: str = "default",
        extra_facts: Optional[List[str]] = None,
    ) -> "CogitationResult":  # noqa: F821 (forward ref)
        """Route a reasoning request through the full Cogitation loop.

        MUST be called from a thread WITHOUT a running event loop (synchronous
        subsystem path).  Returns a CogitationResult; raises on hard error so
        the caller can decide how to handle the failure (governance pillar #1).
        """
        from src.swarm.cogitation import Cogitation, CogitationContext
        ctx = CogitationContext(
            source=self._source,
            domain=domain,
            team=team,
            extra_facts=extra_facts or [],
        )
        return Cogitation().think_sync(goal, ctx)

    async def invoke_reasoning_async(
        self,
        goal: str,
        domain: str = "general",
        team: str = "default",
        extra_facts: Optional[List[str]] = None,
    ) -> "CogitationResult":  # noqa: F821
        """Async variant for async subsystem call sites."""
        from src.swarm.cogitation import Cogitation, CogitationContext
        ctx = CogitationContext(
            source=self._source,
            domain=domain,
            team=team,
            extra_facts=extra_facts or [],
        )
        return await Cogitation().think(goal, ctx)

    # ------------------------------------------------------------------ #
    # Internal trace writer
    # ------------------------------------------------------------------ #

    def _write_trace(
        self,
        *,
        turn_id: str,
        goal_preview: str,
        stages: List[Dict],
        jurisdiction: Dict,
        avg_cs: Optional[float],
        avg_hr: Optional[float],
        hr_tier: Optional[str],
        provenance_summary: str,
        tool_calls: List,
        outcome: str,
        aborted: bool,
        task_run_id: Optional[int],
    ) -> Optional[int]:
        import json as _json
        try:
            from src.database import SessionLocal
            from src.models import DecisionTrace

            trace = DecisionTrace(
                turn_id=turn_id,
                source=self._source,
                goal_preview=goal_preview,
                stages_json=_json.dumps(stages, default=str),
                jurisdiction_json=_json.dumps(jurisdiction, default=str),
                avg_cs=avg_cs,
                avg_hr=avg_hr,
                hr_tier=hr_tier,
                provenance_summary=provenance_summary,
                tool_calls_json=_json.dumps(tool_calls, default=str),
                outcome=outcome,
                aborted=aborted,
                task_run_id=task_run_id,
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
            logger.warning("[CognitionHook:%s] DecisionTrace persist failed: %s",
                           self._subsystem, exc)
            return None
