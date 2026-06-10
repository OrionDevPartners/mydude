"""Fleet API routes — mounted on the main API router under /api/fleet/...

Endpoints:
  GET  /fleet/bots                   — list all bots
  POST /fleet/bots                   — create a bot
  GET  /fleet/bots/{id}              — get bot detail
  POST /fleet/bots/{id}/update       — update bot config
  POST /fleet/bots/{id}/start        — start a bot run (async)
  POST /fleet/bots/{id}/stop         — stop a bot (mark stopped)
  POST /fleet/bots/{id}/delete       — delete a bot

  GET  /fleet/teams                  — list all teams
  POST /fleet/teams                  — create a team
  GET  /fleet/teams/{id}             — get team detail
  POST /fleet/teams/{id}/update      — update team config
  POST /fleet/teams/{id}/start       — start all team bots
  POST /fleet/teams/{id}/stop        — stop team
  POST /fleet/teams/{id}/delete      — delete team

  POST /fleet/spawn                  — spawn a bot into a team (capability-gated)

  POST /fleet/provision/plan         — create a provisioning job (plan phase)
  POST /fleet/provision/{job_id}/approve — approve + apply
  GET  /fleet/provision              — list provisioning jobs + resources
  GET  /fleet/provision/{job_id}     — get job detail

  GET  /fleet/status                 — fleet-wide live status
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException

from src.web.auth import require_auth

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/fleet")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dt(val) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


def _bot_row(b) -> Dict[str, Any]:
    return {
        "id": b.id,
        "name": b.name,
        "description": b.description,
        "team_id": b.team_id,
        "spawned_by_id": b.spawned_by_id,
        "identity_schema": b.identity_schema or {},
        "prompt_cards": b.prompt_cards or [],
        "goal": b.goal,
        "protocols": b.protocols or [],
        "allowed_caps": b.allowed_caps or [],
        "lifecycle": b.lifecycle,
        "last_run_at": _dt(b.last_run_at),
        "last_task_run_id": b.last_task_run_id,
        "created_at": _dt(b.created_at),
        "updated_at": _dt(b.updated_at),
    }


def _team_row(t, member_count: int = 0) -> Dict[str, Any]:
    return {
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "spawn_cap": t.spawn_cap,
        "status": t.status,
        "memory_namespace": t.memory_namespace,
        "member_count": member_count,
        "created_at": _dt(t.created_at),
        "updated_at": _dt(t.updated_at),
    }


def _resource_row(r) -> Dict[str, Any]:
    return {
        "id": r.id,
        "bot_id": r.bot_id,
        "team_id": r.team_id,
        "resource_type": r.resource_type,
        "provider": r.provider,
        "name": r.name,
        "resource_id": r.resource_id,
        "status": r.status,
        "plan_output": r.plan_output,
        "apply_output": r.apply_output,
        "config_json": r.config_json or {},
        "approved_at": _dt(r.approved_at),
        "provisioned_at": _dt(r.provisioned_at),
        "created_at": _dt(r.created_at),
    }


def _job_row(j) -> Dict[str, Any]:
    return {
        "id": j.id,
        "bot_id": j.bot_id,
        "team_id": j.team_id,
        "resource_id": j.resource_id,
        "status": j.status,
        "requested_config": j.requested_config or {},
        "plan_summary": j.plan_summary,
        "apply_summary": j.apply_summary,
        "error": j.error,
        "planned_at": _dt(j.planned_at),
        "approved_at": _dt(j.approved_at),
        "applied_at": _dt(j.applied_at),
        "created_at": _dt(j.created_at),
    }


def _parse_json_field(raw: Optional[str], default=None):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Bot endpoints
# ---------------------------------------------------------------------------

@router.get("/bots")
async def list_bots(_=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import Bot
    db = SessionLocal()
    try:
        bots = db.query(Bot).order_by(Bot.created_at.desc()).all()
        return {"bots": [_bot_row(b) for b in bots]}
    finally:
        db.close()


@router.post("/bots")
async def create_bot(
    name: str = Form(...),
    description: str = Form(""),
    team_id: Optional[str] = Form(None),
    goal: str = Form(""),
    identity_schema: str = Form("{}"),
    prompt_cards: str = Form("[]"),
    protocols: str = Form("[]"),
    allowed_caps: str = Form("[]"),
    _=Depends(require_auth),
):
    from src.database import SessionLocal
    from src.models import Bot

    name = name.strip()
    if not name:
        raise HTTPException(400, "Bot name is required.")

    tid = int(team_id) if team_id and team_id.strip() else None

    db = SessionLocal()
    try:
        bot = Bot(
            name=name,
            description=description.strip() or None,
            team_id=tid,
            goal=goal.strip() or None,
            identity_schema=_parse_json_field(identity_schema, {}),
            prompt_cards=_parse_json_field(prompt_cards, []),
            protocols=_parse_json_field(protocols, []),
            allowed_caps=_parse_json_field(allowed_caps, []),
            lifecycle="defined",
        )
        db.add(bot)
        db.commit()
        db.refresh(bot)
        return {"ok": True, "bot": _bot_row(bot)}
    finally:
        db.close()


@router.get("/bots/{bot_id}")
async def get_bot(bot_id: int, _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import Bot
    db = SessionLocal()
    try:
        bot = db.query(Bot).filter(Bot.id == bot_id).first()
        if not bot:
            raise HTTPException(404, "Bot not found.")
        return {"bot": _bot_row(bot)}
    finally:
        db.close()


@router.post("/bots/{bot_id}/update")
async def update_bot(
    bot_id: int,
    name: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    goal: Optional[str] = Form(None),
    team_id: Optional[str] = Form(None),
    identity_schema: Optional[str] = Form(None),
    prompt_cards: Optional[str] = Form(None),
    protocols: Optional[str] = Form(None),
    allowed_caps: Optional[str] = Form(None),
    _=Depends(require_auth),
):
    from src.database import SessionLocal
    from src.models import Bot
    db = SessionLocal()
    try:
        bot = db.query(Bot).filter(Bot.id == bot_id).first()
        if not bot:
            raise HTTPException(404, "Bot not found.")
        if name is not None:
            bot.name = name.strip()
        if description is not None:
            bot.description = description.strip() or None
        if goal is not None:
            bot.goal = goal.strip() or None
        if team_id is not None:
            bot.team_id = int(team_id) if team_id.strip() else None
        if identity_schema is not None:
            bot.identity_schema = _parse_json_field(identity_schema, bot.identity_schema)
        if prompt_cards is not None:
            bot.prompt_cards = _parse_json_field(prompt_cards, bot.prompt_cards)
        if protocols is not None:
            bot.protocols = _parse_json_field(protocols, bot.protocols)
        if allowed_caps is not None:
            bot.allowed_caps = _parse_json_field(allowed_caps, bot.allowed_caps)
        db.commit()
        db.refresh(bot)
        return {"ok": True, "bot": _bot_row(bot)}
    finally:
        db.close()


@router.post("/bots/{bot_id}/start")
async def start_bot(bot_id: int, background_tasks: BackgroundTasks, goal: str = Form(""), _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import Bot
    db = SessionLocal()
    try:
        bot = db.query(Bot).filter(Bot.id == bot_id).first()
        if not bot:
            raise HTTPException(404, "Bot not found.")
        if bot.lifecycle == "running":
            return {"ok": False, "msg": "Bot is already running."}
    finally:
        db.close()

    goal_override = goal.strip() or None

    from src.fleet.runner import run_bot
    background_tasks.add_task(run_bot, bot_id, goal_override)
    return {"ok": True, "msg": f"Bot {bot_id} started.", "bot_id": bot_id}


@router.post("/bots/{bot_id}/stop")
async def stop_bot(bot_id: int, _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import Bot
    db = SessionLocal()
    try:
        bot = db.query(Bot).filter(Bot.id == bot_id).first()
        if not bot:
            raise HTTPException(404, "Bot not found.")
        bot.lifecycle = "stopped"
        db.commit()
        return {"ok": True, "msg": f"Bot {bot_id} stopped."}
    finally:
        db.close()


@router.post("/bots/{bot_id}/delete")
async def delete_bot(bot_id: int, _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import Bot
    db = SessionLocal()
    try:
        bot = db.query(Bot).filter(Bot.id == bot_id).first()
        if not bot:
            raise HTTPException(404, "Bot not found.")
        db.delete(bot)
        db.commit()
        return {"ok": True, "msg": f"Bot {bot_id} deleted."}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Team endpoints
# ---------------------------------------------------------------------------

@router.get("/teams")
async def list_teams(_=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import Bot, Team
    db = SessionLocal()
    try:
        teams = db.query(Team).order_by(Team.created_at.desc()).all()
        counts = {t.id: db.query(Bot).filter(Bot.team_id == t.id).count() for t in teams}
        return {"teams": [_team_row(t, counts.get(t.id, 0)) for t in teams]}
    finally:
        db.close()


@router.post("/teams")
async def create_team(
    name: str = Form(...),
    description: str = Form(""),
    spawn_cap: str = Form("5"),
    memory_namespace: str = Form(""),
    _=Depends(require_auth),
):
    from src.database import SessionLocal
    from src.models import Team

    name = name.strip()
    if not name:
        raise HTTPException(400, "Team name is required.")

    db = SessionLocal()
    try:
        team = Team(
            name=name,
            description=description.strip() or None,
            spawn_cap=int(spawn_cap) if spawn_cap.strip().isdigit() else 5,
            memory_namespace=memory_namespace.strip() or None,
            status="defined",
        )
        db.add(team)
        db.commit()
        db.refresh(team)
        return {"ok": True, "team": _team_row(team)}
    finally:
        db.close()


@router.get("/teams/{team_id}")
async def get_team(team_id: int, _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import Bot, Team
    db = SessionLocal()
    try:
        team = db.query(Team).filter(Team.id == team_id).first()
        if not team:
            raise HTTPException(404, "Team not found.")
        count = db.query(Bot).filter(Bot.team_id == team_id).count()
        bots = db.query(Bot).filter(Bot.team_id == team_id).all()
        return {"team": _team_row(team, count), "bots": [_bot_row(b) for b in bots]}
    finally:
        db.close()


@router.post("/teams/{team_id}/update")
async def update_team(
    team_id: int,
    name: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    spawn_cap: Optional[str] = Form(None),
    memory_namespace: Optional[str] = Form(None),
    _=Depends(require_auth),
):
    from src.database import SessionLocal
    from src.models import Team
    db = SessionLocal()
    try:
        team = db.query(Team).filter(Team.id == team_id).first()
        if not team:
            raise HTTPException(404, "Team not found.")
        if name is not None:
            team.name = name.strip()
        if description is not None:
            team.description = description.strip() or None
        if spawn_cap is not None and spawn_cap.strip().isdigit():
            team.spawn_cap = int(spawn_cap)
        if memory_namespace is not None:
            team.memory_namespace = memory_namespace.strip() or None
        db.commit()
        db.refresh(team)
        from src.models import Bot as _Bot
        count = db.query(_Bot).filter(
            _Bot.team_id == team_id
        ).count()
        return {"ok": True, "team": _team_row(team, count)}
    finally:
        db.close()


@router.post("/teams/{team_id}/start")
async def start_team(team_id: int, background_tasks: BackgroundTasks, _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import Bot, Team
    db = SessionLocal()
    try:
        team = db.query(Team).filter(Team.id == team_id).first()
        if not team:
            raise HTTPException(404, "Team not found.")
        if team.status == "running":
            return {"ok": False, "msg": "Team is already running."}
        member_count = db.query(Bot).filter(Bot.team_id == team_id).count()
        if member_count == 0:
            return {"ok": False, "msg": "Team has no bots. Add bots to the team before starting it."}
    finally:
        db.close()

    from src.fleet.runner import run_team
    background_tasks.add_task(run_team, team_id)
    return {"ok": True, "msg": f"Team {team_id} started.", "team_id": team_id}


@router.post("/teams/{team_id}/stop")
async def stop_team(team_id: int, _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import Bot, Team
    db = SessionLocal()
    try:
        team = db.query(Team).filter(Team.id == team_id).first()
        if not team:
            raise HTTPException(404, "Team not found.")
        team.status = "stopped"
        db.query(Bot).filter(Bot.team_id == team_id, Bot.lifecycle == "running").update({"lifecycle": "stopped"})
        db.commit()
        return {"ok": True, "msg": f"Team {team_id} stopped."}
    finally:
        db.close()


@router.post("/teams/{team_id}/scale")
async def scale_team(
    team_id: int,
    target_count: int = Form(...),
    goal_template: str = Form(""),
    _=Depends(require_auth),
):
    """Scale a team to target_count bots via the governed spawn pipeline.

    Requires at least one bot already in the team (that bot acts as the spawner).
    All spawns are routed through CapabilityBroker → bot_spawn capability
    (contract validate → policy gate → audit trail).
    """
    from src.database import SessionLocal
    from src.models import Bot, Team
    db = SessionLocal()
    try:
        team = db.query(Team).filter(Team.id == team_id).first()
        if not team:
            raise HTTPException(404, "Team not found.")
        cap = team.spawn_cap or 5
        if target_count < 1:
            raise HTTPException(400, "target_count must be ≥ 1.")
        if target_count > cap:
            raise HTTPException(400, f"target_count {target_count} exceeds team spawn_cap {cap}.")
        bots = db.query(Bot).filter(Bot.team_id == team_id).all()
        current_count = len(bots)
        spawner_bot = bots[0] if bots else None
        team_name = team.name
    finally:
        db.close()

    if target_count <= current_count:
        return {
            "ok": True,
            "msg": f"Team already has {current_count} bots (≥ {target_count}). No scaling needed.",
            "spawned": 0,
            "current_count": current_count,
        }

    if spawner_bot is None:
        raise HTTPException(400, "Team has no bots. Add at least one bot to the team before scaling.")

    from src.fleet.spawner import spawn_bot
    to_spawn = target_count - current_count
    spawned = 0
    errors = []
    for i in range(to_spawn):
        n = current_count + spawned + 1
        goal = goal_template.strip() or f"Execute {team_name} team objective."
        result = await spawn_bot(
            spawner_bot_id=spawner_bot.id,
            name=f"{team_name}-bot-{n}",
            goal=goal,
        )
        if result.get("ok"):
            spawned += 1
        else:
            errors.append(result.get("error", "Unknown error"))

    return {
        "ok": True,
        "msg": f"Scaled team to {current_count + spawned} bots ({spawned} spawned, {len(errors)} failed).",
        "spawned": spawned,
        "errors": errors,
        "current_count": current_count + spawned,
    }


@router.post("/teams/{team_id}/delete")
async def delete_team(team_id: int, _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import Bot, Team
    db = SessionLocal()
    try:
        team = db.query(Team).filter(Team.id == team_id).first()
        if not team:
            raise HTTPException(404, "Team not found.")
        db.query(Bot).filter(Bot.team_id == team_id).update({"team_id": None})
        db.delete(team)
        db.commit()
        return {"ok": True, "msg": f"Team {team_id} deleted."}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Spawn endpoint
# ---------------------------------------------------------------------------

@router.post("/spawn")
async def spawn_bot_endpoint(
    spawner_bot_id: int = Form(...),
    name: str = Form(...),
    goal: str = Form(""),
    identity_schema: str = Form("{}"),
    prompt_cards: str = Form("[]"),
    protocols: str = Form("[]"),
    _=Depends(require_auth),
):
    from src.fleet.spawner import spawn_bot
    result = await spawn_bot(
        spawner_bot_id=spawner_bot_id,
        name=name.strip(),
        goal=goal.strip(),
        identity_schema=_parse_json_field(identity_schema),
        prompt_cards=_parse_json_field(prompt_cards, []),
        protocols=_parse_json_field(protocols, []),
    )
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Spawn failed."))
    return result


# ---------------------------------------------------------------------------
# Provisioning endpoints
# ---------------------------------------------------------------------------

@router.post("/provision/plan")
async def provision_plan(
    resource_type: str = Form(...),
    config: str = Form("{}"),
    bot_id: Optional[str] = Form(None),
    team_id: Optional[str] = Form(None),
    _=Depends(require_auth),
):
    from src.fleet.provisioner import create_provisioning_job
    cfg = _parse_json_field(config, {})
    bid = int(bot_id) if bot_id and bot_id.strip().isdigit() else None
    tid = int(team_id) if team_id and team_id.strip().isdigit() else None
    result = await create_provisioning_job(resource_type, cfg, bot_id=bid, team_id=tid)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Plan failed."))
    return result


@router.post("/provision/{job_id}/approve")
async def provision_approve(job_id: int, _=Depends(require_auth)):
    from src.fleet.provisioner import approve_provisioning_job
    result = await approve_provisioning_job(job_id)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Apply failed."))
    return result


@router.get("/provision")
async def list_provisioning(_=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import ProvisionedResource, ProvisioningJob
    db = SessionLocal()
    try:
        jobs = db.query(ProvisioningJob).order_by(ProvisioningJob.created_at.desc()).limit(100).all()
        resources = db.query(ProvisionedResource).order_by(ProvisionedResource.created_at.desc()).limit(100).all()
        return {
            "jobs": [_job_row(j) for j in jobs],
            "resources": [_resource_row(r) for r in resources],
        }
    finally:
        db.close()


@router.get("/provision/{job_id}")
async def get_provisioning_job(job_id: int, _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import ProvisionedResource, ProvisioningJob
    db = SessionLocal()
    try:
        job = db.query(ProvisioningJob).filter(ProvisioningJob.id == job_id).first()
        if not job:
            raise HTTPException(404, "Provisioning job not found.")
        resource = db.query(ProvisionedResource).filter(ProvisionedResource.id == job.resource_id).first() if job.resource_id else None
        return {
            "job": _job_row(job),
            "resource": _resource_row(resource) if resource else None,
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Fleet status
# ---------------------------------------------------------------------------

@router.get("/status")
async def fleet_status(_=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import Bot, ProvisionedResource, ProvisioningJob, Team
    db = SessionLocal()
    try:
        bots = db.query(Bot).all()
        teams = db.query(Team).all()
        resources = db.query(ProvisionedResource).all()
        jobs_awaiting = db.query(ProvisioningJob).filter(ProvisioningJob.status == "awaiting_approval").count()

        lifecycle_counts: Dict[str, int] = {}
        for b in bots:
            lifecycle_counts[b.lifecycle] = lifecycle_counts.get(b.lifecycle, 0) + 1

        team_counts: Dict[str, int] = {}
        for t in teams:
            team_counts[t.status] = team_counts.get(t.status, 0) + 1

        resource_counts: Dict[str, int] = {}
        for r in resources:
            resource_counts[r.status] = resource_counts.get(r.status, 0) + 1

        return {
            "total_bots": len(bots),
            "total_teams": len(teams),
            "total_resources": len(resources),
            "jobs_awaiting_approval": jobs_awaiting,
            "bot_lifecycle": lifecycle_counts,
            "team_status": team_counts,
            "resource_status": resource_counts,
        }
    finally:
        db.close()
