"""BotRunner — translates a persisted Bot/Team into swarm orchestrator inputs
and executes a governed run, wiring the bot's identity + protocols into the
PORTER/WORKER persona layers the orchestrator already consumes.

Memory integration: each bot/team writes and reads from a namespaced slice of
the shared Cognee/Mem0 substrate so context accumulates across runs.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _build_persona_prompt(bot_row) -> str:
    """Convert a Bot DB row's identity schema + prompt cards + protocols into
    a persona prompt string the swarm orchestrator prepends to the system prompt."""
    parts: List[str] = []

    identity = bot_row.identity_schema or {}
    if identity:
        role = identity.get("role", bot_row.name)
        personality = identity.get("personality", "")
        style = identity.get("communication_style", "")
        parts.append(f"BOT IDENTITY: {bot_row.name}")
        parts.append(f"ROLE: {role}")
        if personality:
            parts.append(f"PERSONALITY: {personality}")
        if style:
            parts.append(f"COMMUNICATION STYLE: {style}")

    cards: List[str] = bot_row.prompt_cards or []
    if cards:
        parts.append("PROMPT CARDS:")
        for card in cards:
            parts.append(f"  - {card}")

    protocols: List[str] = bot_row.protocols or []
    if protocols:
        parts.append("OPERATOR PROTOCOLS:")
        for p in protocols:
            parts.append(f"  - {p}")

    allowed_caps: List[str] = bot_row.allowed_caps or []
    if allowed_caps:
        parts.append(f"ALLOWED CAPABILITIES: {', '.join(allowed_caps)}")

    return "\n".join(parts)


def _resolve_memory_namespace(bot_row, team_row=None) -> Optional[str]:
    if team_row and team_row.memory_namespace:
        return team_row.memory_namespace
    if team_row:
        return f"team:{team_row.id}:{team_row.name}"
    return f"bot:{bot_row.id}:{bot_row.name}"


async def run_bot(
    bot_id: int,
    goal_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a single bot run through the governed swarm orchestrator.

    Returns the orchestrator result dict, augmented with fleet metadata.
    Never raises — returns an error dict on failure so the lifecycle state
    machine can handle it cleanly.
    """
    from src.database import SessionLocal
    from src.models import Bot, Team, TaskRun

    db = SessionLocal()
    try:
        bot = db.query(Bot).filter(Bot.id == bot_id).first()
        if not bot:
            return {"ok": False, "error": f"Bot {bot_id} not found"}

        team = db.query(Team).filter(Team.id == bot.team_id).first() if bot.team_id else None

        goal = goal_override or bot.goal or f"Execute bot goal for {bot.name}"
        persona_prompt = _build_persona_prompt(bot)
        namespace = _resolve_memory_namespace(bot, team)
        domain = (bot.identity_schema or {}).get("domain", "general")
        team_name = team.name if team else "default"

        bot.lifecycle = "running"
        db.commit()

        task_run = TaskRun(
            prompt=f"[BOT:{bot.name}] {goal}",
            status="running",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)
    except Exception as e:
        logger.error("BotRunner setup failed for bot %s: %s", bot_id, e)
        db.close()
        return {"ok": False, "error": str(e)}
    finally:
        pass

    try:
        from src.swarm.broker import CapabilityBroker
        from src.swarm.policy import PolicyEngine
        from src.swarm.integrations import Integrations
        from src.swarm.orchestrator import WaveOrchestrator

        allowed_caps = bot.allowed_caps or []
        policy = PolicyEngine()

        class BotCapabilityBroker(CapabilityBroker):
            """Restrict this bot to its declared allowed_caps list."""
            async def request(self, capability, params):
                if allowed_caps and capability not in allowed_caps:
                    from src.swarm.policy import PolicyDecision
                    from src.swarm.broker import BrokerResult
                    return BrokerResult(
                        False,
                        PolicyDecision(False, f"Bot '{bot.name}' is not permitted to use capability '{capability}'"),
                        None,
                    )
                return await super().request(capability, params)

        broker = BotCapabilityBroker(policy, Integrations())
        orch = WaveOrchestrator(broker)

        augmented_goal = f"{persona_prompt}\n\nGOAL:\n{goal}" if persona_prompt else goal

        import uuid
        from src.memory import get_substrate
        mem = get_substrate()

        recalled = []
        try:
            recalled = mem.inject_for_task(f"{namespace}:{goal}", top_k=5)
        except Exception as e:
            logger.warning("BotRunner memory recall failed: %s", e)

        if recalled:
            augmented_goal += "\n\nRECALLED MEMORY:\n" + "\n".join(recalled)

        result = await orch.run(
            goal=augmented_goal,
            domain=domain,
            team=team_name,
            task_run_id=task_run.id,
        )

        try:
            mem.persist_handoff(
                goal=f"{namespace}:{goal}",
                facts=result.get("FACTS", []),
                decisions=result.get("DECISIONS", []),
                claim_ledger_summary=str(result.get("CLAIM_LEDGER", "")),
                session_id=str(task_run.id),
            )
        except Exception as e:
            logger.warning("BotRunner memory persist failed: %s", e)

        import json
        db2 = SessionLocal()
        try:
            tr = db2.query(TaskRun).filter(TaskRun.id == task_run.id).first()
            if tr:
                tr.result = json.dumps(result, default=str)
                tr.status = "complete"
            b = db2.query(Bot).filter(Bot.id == bot_id).first()
            if b:
                from datetime import datetime
                b.lifecycle = "stopped"
                b.last_run_at = datetime.utcnow()
                b.last_task_run_id = task_run.id
            db2.commit()
        finally:
            db2.close()

        db.close()
        return {"ok": True, "bot_id": bot_id, "task_run_id": task_run.id, "result": result}

    except Exception as e:
        logger.error("BotRunner execution failed for bot %s: %s", bot_id, e)
        try:
            db2 = SessionLocal()
            b = db2.query(Bot).filter(Bot.id == bot_id).first()
            if b:
                b.lifecycle = "failed"
            db2.commit()
            db2.close()
        except Exception:
            pass
        db.close()
        return {"ok": False, "bot_id": bot_id, "error": str(e)}


async def run_team(team_id: int) -> Dict[str, Any]:
    """Run all bots in a team concurrently, sharing the swarm orchestrator's
    memory substrate so context accumulates across all members."""
    from src.database import SessionLocal
    from src.models import Bot, Team

    db = SessionLocal()
    try:
        team = db.query(Team).filter(Team.id == team_id).first()
        if not team:
            return {"ok": False, "error": f"Team {team_id} not found"}
        bots = db.query(Bot).filter(Bot.team_id == team_id).all()
        bot_ids = [b.id for b in bots]
        if not bot_ids:
            return {"ok": False, "error": "Team has no bots"}
        team.status = "running"
        db.commit()
    finally:
        db.close()

    tasks = [run_bot(bid) for bid in bot_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    outcomes = []
    for bid, r in zip(bot_ids, results):
        if isinstance(r, Exception):
            outcomes.append({"bot_id": bid, "ok": False, "error": str(r)})
        else:
            outcomes.append(r)

    db2 = SessionLocal()
    try:
        t = db2.query(Team).filter(Team.id == team_id).first()
        if t:
            t.status = "stopped"
        db2.commit()
    finally:
        db2.close()

    return {"ok": True, "team_id": team_id, "bot_results": outcomes}
