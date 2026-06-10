"""Opt-in scheduled finance sync.

Mirrors ``HealthMonitor``: an asyncio task started from the app startup chain. The
loop is always created, but each tick is a no-op unless the ``ENABLE_FINANCE_AUTOSYNC``
app setting is "1". When enabled it runs a READ-ONLY ``sync_all`` (never any
write-back) in a worker thread so the event loop is not blocked. Toggling the
setting takes effect on the next tick — no restart required.
"""
import asyncio
import logging

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL = 3600  # 1 hour
_ENABLE_KEY = "ENABLE_FINANCE_AUTOSYNC"


class FinanceScheduler:
    def __init__(self):
        self._task = None

    async def start(self, interval=_DEFAULT_INTERVAL):
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(interval))
        logger.info("FinanceScheduler started (interval=%ds, opt-in)", interval)

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
                await self._maybe_sync()
            except Exception:
                logger.exception("FinanceScheduler tick error")
            await asyncio.sleep(interval)

    async def _maybe_sync(self):
        from src.web.settings_store import get_setting
        if (get_setting(_ENABLE_KEY, "0") or "0") != "1":
            return
        await asyncio.to_thread(self._run_sync)

    def _run_sync(self):
        from src.database import SessionLocal
        from src.finance.sync import sync_all
        db = SessionLocal()
        try:
            report = sync_all(db, trigger="scheduled")
            logger.info("Scheduled finance sync: ok=%s", report.get("ok"))
        finally:
            db.close()


_scheduler = FinanceScheduler()


def get_scheduler():
    return _scheduler
