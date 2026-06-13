"""Tests for the fleet engine's three governance-critical paths.

The fleet engine has three governance-critical paths that must never regress
silently — these tests pin them down:

  1. Per-team spawn cap (``BotSpawner.spawn_bot`` / ``_do_spawn``):
       - a spawn at the team's spawn_cap is refused with a clear error
       - a spawn under the cap succeeds and records the lineage (spawned_by_id)
       - a spawner that is not in a team is refused
  2. Plan -> approve -> apply provisioning gate (``FleetProvisioner``):
       - ``create_provisioning_job`` parks a job in ``awaiting_approval``
         with NO real resource created
       - ``approve_provisioning_job`` only applies a job in ``awaiting_approval``;
         a second approve (now ``done``) is refused
       - approving a non-existent job is refused
  3. Bot lifecycle transitions (``BotRunner.run_bot``):
       - the bot moves defined -> running -> stopped, committed to the DB at
         each phase (verified mid-run), with last_run_at / last_task_run_id set

Plus the fleet API layer (``src.fleet.api_routes``) returning the correct
status codes for not-found, already-running, empty-name, bad-spawner, and the
provisioning approve/get error paths.

These run against the live PostgreSQL DB (the Replit built-in) since the fleet
modules persist Bot/Team/ProvisioningJob/ProvisionedResource rows. The swarm /
LLM is never touched: the spawn/provision paths are pure DB writes, and the
lifecycle test stubs the cognition entrypoint with a fake so no provider or
network is exercised. git_repo is used for provisioning because it is the one
resource type allowed without ALLOW_FLEET_PROVISIONING and never reaches a real
provider without GITHUB_TOKEN (it returns a deterministic stub).

Runnable two ways:
  * ``python tests/test_fleet_governance.py``  (standalone, exits non-zero on failure)
  * ``pytest tests/test_fleet_governance.py``   (test_* functions; no plugins needed)
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DEV_AUTH_BYPASS", "1")
os.environ.pop("REPLIT_DEPLOYMENT", None)
# Spawning is enabled by default, but pin it so the suite is independent of the
# operator's current environment.
os.environ["ENABLE_BOT_SPAWN"] = "true"

from src.database import SessionLocal
from src.models import Bot, Team, ProvisioningJob, ProvisionedResource, TaskRun


_PREFIX = "ZZTEST_FLEET_"


# -- helpers -----------------------------------------------------------------

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_team(spawn_cap: int) -> int:
    db = SessionLocal()
    try:
        t = Team(name=f"{_PREFIX}team", spawn_cap=spawn_cap, status="defined")
        db.add(t)
        db.commit()
        db.refresh(t)
        return t.id
    finally:
        db.close()


def _make_bot(team_id=None, name="bot", goal="do a thing", allowed_caps=None) -> int:
    db = SessionLocal()
    try:
        b = Bot(
            name=f"{_PREFIX}{name}",
            team_id=team_id,
            goal=goal,
            identity_schema={"role": "tester"},
            prompt_cards=[],
            protocols=[],
            allowed_caps=allowed_caps or [],
            lifecycle="defined",
        )
        db.add(b)
        db.commit()
        db.refresh(b)
        return b.id
    finally:
        db.close()


def _bot_lifecycle(bot_id: int):
    db = SessionLocal()
    try:
        b = db.query(Bot).filter(Bot.id == bot_id).first()
        return b.lifecycle if b else None
    finally:
        db.close()


def _cleanup():
    """Remove every row this suite could have created (idempotent)."""
    db = SessionLocal()
    try:
        team_ids = [t.id for t in db.query(Team).filter(Team.name.like(f"{_PREFIX}%")).all()]
        bot_ids = [b.id for b in db.query(Bot).filter(Bot.name.like(f"{_PREFIX}%")).all()]
        # Jobs/resources are tied to the test teams/bots.
        if team_ids or bot_ids:
            jobs = db.query(ProvisioningJob).filter(
                (ProvisioningJob.team_id.in_(team_ids or [-1]))
                | (ProvisioningJob.bot_id.in_(bot_ids or [-1]))
            ).all()
            for j in jobs:
                db.delete(j)
            resources = db.query(ProvisionedResource).filter(
                (ProvisionedResource.team_id.in_(team_ids or [-1]))
                | (ProvisionedResource.bot_id.in_(bot_ids or [-1]))
            ).all()
            for r in resources:
                db.delete(r)
        # Break self-referential FKs (spawned_by_id) and team FKs before deleting
        # so the bulk delete cannot trip bots_spawned_by_id_fkey / team FKs.
        db.query(Bot).filter(Bot.name.like(f"{_PREFIX}%")).update(
            {"spawned_by_id": None, "team_id": None}, synchronize_session=False)
        db.commit()
        for b in db.query(Bot).filter(Bot.name.like(f"{_PREFIX}%")).all():
            db.delete(b)
        db.commit()
        for t in db.query(Team).filter(Team.name.like(f"{_PREFIX}%")).all():
            db.delete(t)
        # TaskRuns created by the lifecycle test carry the bot-name marker.
        for tr in db.query(TaskRun).filter(TaskRun.prompt.like(f"%{_PREFIX}%")).all():
            db.delete(tr)
        db.commit()
    finally:
        db.close()


# ===========================================================================
# 1. Spawn cap enforcement
# ===========================================================================

def test_spawn_under_cap_succeeds_and_records_lineage():
    """A spawn below the cap creates a new bot and records spawned_by_id."""
    team_id = _make_team(spawn_cap=3)
    spawner_id = _make_bot(team_id=team_id, name="spawner")

    result = _run(__import__("src.fleet.spawner", fromlist=["spawn_bot"]).spawn_bot(
        spawner_bot_id=spawner_id,
        name=f"{_PREFIX}child",
        goal="child goal",
    ))

    assert result.get("ok") is True, result
    new_id = result.get("bot_id")
    assert new_id, result

    db = SessionLocal()
    try:
        child = db.query(Bot).filter(Bot.id == new_id).first()
        assert child is not None, "spawned bot must be persisted"
        assert child.team_id == team_id, child.team_id
        assert child.spawned_by_id == spawner_id, "lineage (spawned_by_id) must be recorded"
        assert child.lifecycle == "defined", child.lifecycle
    finally:
        db.close()


def test_spawn_at_cap_is_refused_with_clear_error():
    """When the team is already at spawn_cap, spawn_bot returns a clear error
    and writes NO new bot row."""
    team_id = _make_team(spawn_cap=2)
    spawner_id = _make_bot(team_id=team_id, name="spawner")
    _make_bot(team_id=team_id, name="member2")  # team now holds 2 == cap

    db = SessionLocal()
    try:
        before = db.query(Bot).filter(Bot.team_id == team_id).count()
    finally:
        db.close()
    assert before == 2, before

    result = _run(__import__("src.fleet.spawner", fromlist=["spawn_bot"]).spawn_bot(
        spawner_bot_id=spawner_id,
        name=f"{_PREFIX}overflow",
        goal="should be blocked",
    ))

    assert result.get("ok") is False, result
    assert "spawn cap" in result.get("error", "").lower(), result

    db = SessionLocal()
    try:
        after = db.query(Bot).filter(Bot.team_id == team_id).count()
    finally:
        db.close()
    assert after == before, "no bot row may be created once the cap is reached"


def test_spawn_requires_a_team():
    """A solo bot (no team) cannot spawn — spawning requires a team."""
    spawner_id = _make_bot(team_id=None, name="solo")
    result = _run(__import__("src.fleet.spawner", fromlist=["spawn_bot"]).spawn_bot(
        spawner_bot_id=spawner_id,
        name=f"{_PREFIX}orphan",
        goal="no team",
    ))
    assert result.get("ok") is False, result
    assert "team" in result.get("error", "").lower(), result


# ===========================================================================
# 2. Provisioning plan -> approve -> apply gate
# ===========================================================================

def test_provision_plan_parks_job_awaiting_approval_without_creating_resource():
    """plan() records a job in awaiting_approval and a pending_approval resource
    — but nothing is actually provisioned yet."""
    team_id = _make_team(spawn_cap=5)
    prov = __import__("src.fleet.provisioner", fromlist=["create_provisioning_job"])

    result = _run(prov.create_provisioning_job(
        "git_repo",
        {"name": f"{_PREFIX}repo", "private": True},
        team_id=team_id,
    ))
    assert result.get("ok") is True, result
    assert result.get("status") == "awaiting_approval", result
    job_id = result.get("job_id")
    assert job_id, result

    db = SessionLocal()
    try:
        job = db.query(ProvisioningJob).filter(ProvisioningJob.id == job_id).first()
        assert job is not None and job.status == "awaiting_approval", job
        res = db.query(ProvisionedResource).filter(ProvisionedResource.id == job.resource_id).first()
        assert res is not None, "a resource record must exist for the plan"
        # Plan phase: NOT yet provisioned.
        assert res.status == "pending_approval", res.status
        assert res.resource_id in (None, ""), "no provider id before approval"
        assert res.approved_at is None, "approved_at must be unset before approval"
    finally:
        db.close()


def test_approve_only_applies_an_awaiting_approval_job():
    """approve_provisioning_job applies an awaiting_approval job exactly once;
    a second approve (now 'done') is refused."""
    team_id = _make_team(spawn_cap=5)
    prov = __import__("src.fleet.provisioner", fromlist=[
        "create_provisioning_job", "approve_provisioning_job"])

    plan = _run(prov.create_provisioning_job(
        "git_repo", {"name": f"{_PREFIX}repo2", "private": True}, team_id=team_id))
    job_id = plan["job_id"]

    # First approve: applies the plan (git_repo with no token -> deterministic stub).
    applied = _run(prov.approve_provisioning_job(job_id))
    assert applied.get("ok") is True, applied
    assert applied.get("status") == "done", applied

    db = SessionLocal()
    try:
        job = db.query(ProvisioningJob).filter(ProvisioningJob.id == job_id).first()
        assert job.status == "done", job.status
        assert job.approved_at is not None, "approved_at must be stamped on apply"
        assert job.applied_at is not None, "applied_at must be stamped on apply"
        res = db.query(ProvisionedResource).filter(ProvisionedResource.id == job.resource_id).first()
        assert res.status == "active", res.status
        assert res.resource_id, "an applied resource must record its provider id"
    finally:
        db.close()

    # Second approve: the job is no longer awaiting_approval, so it is refused.
    again = _run(prov.approve_provisioning_job(job_id))
    assert again.get("ok") is False, again
    assert "awaiting_approval" in again.get("error", ""), again


def test_approve_unknown_job_is_refused():
    prov = __import__("src.fleet.provisioner", fromlist=["approve_provisioning_job"])
    result = _run(prov.approve_provisioning_job(99_999_999))
    assert result.get("ok") is False, result
    assert "not found" in result.get("error", "").lower(), result


# ===========================================================================
# 3. Bot lifecycle transitions (defined -> running -> stopped)
# ===========================================================================

class _FakeCogResult:
    def __init__(self, result):
        self.result = result
        self.turn_id = "fake-turn-id"
        self.decision_trace_id = None


class _FakeCog:
    """Stands in for the runner's _BotCogitation so no swarm/LLM is touched.

    During think() it reads the live bot row from a fresh session to prove the
    runner committed lifecycle='running' BEFORE invoking cognition.
    """
    captured = {}

    def __init__(self, allowed_caps, bot_name):
        self.allowed_caps = allowed_caps
        self.bot_name = bot_name

    async def think(self, goal, ctx):
        db = SessionLocal()
        try:
            b = (
                db.query(Bot)
                .filter(Bot.name == self.bot_name)
                .order_by(Bot.id.desc())
                .first()
            )
            _FakeCog.captured["mid_lifecycle"] = b.lifecycle if b else None
        finally:
            db.close()
        return _FakeCogResult({"SYNTHESIS": "stub answer", "GOAL": goal})


def test_run_bot_records_defined_running_stopped():
    """run_bot drives defined -> running -> stopped, committed at each phase."""
    import src.fleet.runner as runner

    team_id = _make_team(spawn_cap=5)
    bot_id = _make_bot(team_id=team_id, name="lifecycle")
    assert _bot_lifecycle(bot_id) == "defined", "bot starts in 'defined'"

    _FakeCog.captured.clear()
    original = runner._BotCogitation
    runner._BotCogitation = _FakeCog
    try:
        result = _run(runner.run_bot(bot_id))
    finally:
        runner._BotCogitation = original

    assert result.get("ok") is True, result
    # Mid-run (inside think) the bot must already be 'running' in the DB.
    assert _FakeCog.captured.get("mid_lifecycle") == "running", _FakeCog.captured
    # And the terminal transition to 'stopped' must be persisted.
    assert _bot_lifecycle(bot_id) == "stopped", "bot must end 'stopped'"

    db = SessionLocal()
    try:
        b = db.query(Bot).filter(Bot.id == bot_id).first()
        assert b.last_run_at is not None, "last_run_at must be stamped"
        assert b.last_task_run_id == result.get("task_run_id"), b.last_task_run_id
        tr = db.query(TaskRun).filter(TaskRun.id == b.last_task_run_id).first()
        assert tr is not None and tr.status == "complete", tr
    finally:
        db.close()


def test_run_bot_unknown_bot_returns_error():
    import src.fleet.runner as runner
    result = _run(runner.run_bot(99_999_999))
    assert result.get("ok") is False, result
    assert "not found" in result.get("error", "").lower(), result


# ===========================================================================
# 4. Fleet API status codes
# ===========================================================================

def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.fleet.api_routes import router as fleet_router

    app = FastAPI()
    app.include_router(fleet_router, prefix="/api")
    return TestClient(app)


def test_api_not_found_paths_return_404():
    c = _client()
    assert c.get("/api/fleet/bots/99999999").status_code == 404
    assert c.post("/api/fleet/bots/99999999/stop").status_code == 404
    assert c.post("/api/fleet/bots/99999999/delete").status_code == 404
    assert c.post("/api/fleet/bots/99999999/start", data={"goal": ""}).status_code == 404
    assert c.get("/api/fleet/teams/99999999").status_code == 404
    assert c.post("/api/fleet/teams/99999999/start").status_code == 404
    assert c.get("/api/fleet/provision/99999999").status_code == 404


def test_api_start_already_running_bot_is_a_noop():
    c = _client()
    bot_id = _make_bot(name="apirunning")
    db = SessionLocal()
    try:
        db.query(Bot).filter(Bot.id == bot_id).update({"lifecycle": "running"})
        db.commit()
    finally:
        db.close()

    resp = c.post(f"/api/fleet/bots/{bot_id}/start", data={"goal": ""})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("ok") is False and "already running" in body.get("msg", "").lower(), body
    # A no-op start must NOT have scheduled a real run / flipped state.
    assert _bot_lifecycle(bot_id) == "running", "no-op start must leave state untouched"


def test_api_create_bot_requires_name():
    c = _client()
    assert c.post("/api/fleet/bots", data={"name": "   "}).status_code == 400


def test_api_create_team_requires_name():
    c = _client()
    assert c.post("/api/fleet/teams", data={"name": "   "}).status_code == 400


def test_api_spawn_bad_spawner_returns_400():
    c = _client()
    resp = c.post("/api/fleet/spawn", data={
        "spawner_bot_id": "99999999",
        "name": f"{_PREFIX}apispawn",
    })
    assert resp.status_code == 400, resp.text


def test_api_approve_unknown_job_returns_400():
    c = _client()
    assert c.post("/api/fleet/provision/99999999/approve").status_code == 400


def test_api_scale_over_cap_returns_400():
    c = _client()
    team_id = _make_team(spawn_cap=2)
    _make_bot(team_id=team_id, name="scaleseed")
    resp = c.post(f"/api/fleet/teams/{team_id}/scale", data={"target_count": "5"})
    assert resp.status_code == 400, resp.text


def teardown_module(module=None):
    """pytest calls this once after the module's tests; remove all test rows."""
    _cleanup()


# -- standalone runner -------------------------------------------------------

def _main():
    failures = 0
    tests = [
        (name, fn) for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    try:
        for name, fn in tests:
            try:
                fn()
                print("PASS %s" % name)
            except AssertionError as e:
                failures += 1
                print("FAIL %s: %s" % (name, e))
            except Exception as e:  # noqa: BLE001
                failures += 1
                import traceback
                print("ERROR %s: %s" % (name, e))
                traceback.print_exc()
    finally:
        _cleanup()
    if failures:
        print("\n%d test(s) failed." % failures)
        sys.exit(1)
    print("\nAll fleet governance tests passed.")


if __name__ == "__main__":
    _main()
