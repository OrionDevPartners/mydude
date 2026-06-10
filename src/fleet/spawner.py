"""BotSpawner — governed bounded spawning.

A running bot may request "spawn another bot into my team" through the
capability broker.  This module enforces the operator-set per-team cap and
records the spawning relationship so the lineage is fully auditable.

Spawn requests flow through the full broker pipeline:
  1. Contract validation — required fields, type checks
  2. Policy engine      — bot_spawn capability gated by ENABLE_BOT_SPAWN
  3. Integrations       — calls _do_spawn, which writes to DB
  4. Audit trail        — every spawn attempt is recorded in CapabilityAuditLog

The public spawn_bot() function builds a broker and routes through it.
_do_spawn() is the private DB-write implementation, called only by
integrations.bot_spawn after the broker has granted permission.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_TRUTHY = ("1", "true", "yes", "on")


def spawn_enabled() -> bool:
    return os.environ.get("ENABLE_BOT_SPAWN", "true").lower() in _TRUTHY


async def _do_spawn(params: Dict[str, Any]) -> Dict[str, Any]:
    """Private implementation: write a new Bot row to the DB.

    Called ONLY by Integrations.bot_spawn after the broker has:
      - validated the contract
      - passed the policy gate
    Never call this directly from API routes or agent code.
    """
    spawner_bot_id = params.get("spawner_bot_id")
    name = (params.get("name") or "").strip()
    goal = (params.get("goal") or "").strip()
    identity_schema = params.get("identity_schema") or {}
    prompt_cards = params.get("prompt_cards") or []
    protocols = params.get("protocols") or []

    if not spawn_enabled():
        return {"ok": False, "error": "Bot spawning is disabled (ENABLE_BOT_SPAWN)."}

    from src.database import SessionLocal
    from src.models import Bot, Team

    db = SessionLocal()
    try:
        spawner = db.query(Bot).filter(Bot.id == spawner_bot_id).first()
        if not spawner:
            return {"ok": False, "error": f"Spawning bot {spawner_bot_id} not found."}

        if not spawner.team_id:
            return {"ok": False, "error": "Bot is not in a team; spawning requires a team."}

        team = db.query(Team).filter(Team.id == spawner.team_id).first()
        if not team:
            return {"ok": False, "error": "Team not found."}

        current_count = db.query(Bot).filter(Bot.team_id == team.id).count()
        cap = team.spawn_cap or 5
        if current_count >= cap:
            return {
                "ok": False,
                "error": (
                    f"Team '{team.name}' has reached its spawn cap ({cap} bots). "
                    "Increase the team's spawn_cap to allow more members."
                ),
            }

        new_bot = Bot(
            name=name,
            goal=goal,
            team_id=team.id,
            spawned_by_id=spawner_bot_id,
            identity_schema=identity_schema or spawner.identity_schema,
            prompt_cards=prompt_cards or spawner.prompt_cards,
            protocols=protocols or spawner.protocols,
            allowed_caps=spawner.allowed_caps,
            lifecycle="defined",
        )
        db.add(new_bot)
        db.commit()
        db.refresh(new_bot)
        new_id = new_bot.id
        logger.info(
            "BotSpawner: spawned bot %s (id=%d) into team %s (id=%d) by bot %d",
            name, new_id, team.name, team.id, spawner_bot_id,
        )
        return {"ok": True, "bot_id": new_id, "team_id": team.id, "name": name}
    except Exception as e:
        logger.error("BotSpawner _do_spawn failed: %s", e)
        return {"ok": False, "error": str(e)}
    finally:
        db.close()


async def spawn_bot(
    spawner_bot_id: int,
    name: str,
    goal: str,
    identity_schema: Optional[Dict] = None,
    prompt_cards: Optional[list] = None,
    protocols: Optional[list] = None,
) -> Dict[str, Any]:
    """Public broker-gated spawn.

    Routes through the full contract → policy → integration → audit pipeline
    (CapabilityBroker.request("bot_spawn", ...)).  The broker dispatches to
    Integrations.bot_spawn which calls _do_spawn and records the audit log.

    Returns ``{"ok": True, "bot_id": <new id>}`` or ``{"ok": False, "error": ...}``.
    Never raises — spawn failures are surfaced as an error dict.
    """
    import json
    from src.swarm.broker import CapabilityBroker
    from src.swarm.integrations import Integrations
    from src.swarm.policy import PolicyEngine

    broker = CapabilityBroker(PolicyEngine(), Integrations())
    result = await broker.request("bot_spawn", {
        "spawner_bot_id": spawner_bot_id,
        "name": name,
        "goal": goal,
        "identity_schema": identity_schema or {},
        "prompt_cards": prompt_cards or [],
        "protocols": protocols or [],
        "source": "fleet_api",
    })
    if not result.ok:
        logger.warning("BotSpawner: broker blocked spawn for bot %s: %s", spawner_bot_id, result.decision.reason)
        return {"ok": False, "error": result.decision.reason}
    try:
        return json.loads(result.output)
    except Exception:
        return {"ok": True, "raw_output": result.output}
