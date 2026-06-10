"""Opt-in scheduled coach reflection.

Mirrors ``FinanceScheduler``: an asyncio task started from the app startup chain.
The loop is always created, but each tick is a no-op unless the
``ENABLE_COACH_REFLECTION`` app setting is "1". When enabled it (1) derives
behavior signals and (2) runs reflection, in a worker thread so the event loop is
not blocked. Toggling the setting takes effect on the next tick — no restart
required. Reflection fails loud (no provider) inside the tick; the loop logs and
continues.
"""
import asyncio
import logging

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL = 21600  # 6 hours
_ENABLE_KEY = "ENABLE_COACH_REFLECTION"


class CoachScheduler:
    def __init__(self):
        self._task = None

    async def start(self, interval=_DEFAULT_INTERVAL):
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(interval))
        logger.info("CoachScheduler started (interval=%ds, opt-in)", interval)

    async def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self, interval):
        while True:
            try:
                await self._maybe_run()
            except Exception:
                logger.exception("CoachScheduler tick error")
            await asyncio.sleep(interval)

    async def _maybe_run(self):
        from src.web.settings_store import get_setting
        if (get_setting(_ENABLE_KEY, "0") or "0") != "1":
            return
        await asyncio.to_thread(self._run)

    def _run(self):
        from src.database import SessionLocal
        from src.coach.behavior import compute_signals
        from src.coach.reflection import run_reflection
        db = SessionLocal()
        try:
            behavior = compute_signals(db, persist=True)
            reflection = run_reflection(db)
            logger.info(
                "Coach reflection tick: behavior_written=%d, reflect_status=%s",
                len(behavior.get("written", [])), reflection.get("status"),
            )
        except Exception:
            logger.exception("Coach reflection run failed")
        finally:
            db.close()


_scheduler = CoachScheduler()


def get_scheduler():
    return _scheduler
