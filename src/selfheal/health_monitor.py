import asyncio
import logging
import os
import shutil
import time
from typing import Callable, Dict, Optional, Awaitable

from src.selfheal.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)


class HealthMonitor:
    def __init__(
        self,
        circuit_breaker: Optional[CircuitBreaker] = None,
        alert_callback: Optional[Callable[[str], Awaitable[None]]] = None,
    ):
        self.circuit_breaker = circuit_breaker
        self.alert_callback = alert_callback
        self._task: Optional[asyncio.Task] = None
        self._last_results: Dict = {}
        self._previous_critical: set = set()

    async def start(self, interval: int = 120) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(interval))
        logger.info("HealthMonitor started with interval=%ds", interval)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            logger.info("HealthMonitor stopped")

    async def _loop(self, interval: int) -> None:
        while True:
            try:
                await self.run_checks()
            except Exception:
                logger.exception("HealthMonitor check loop error")
            await asyncio.sleep(interval)

    async def run_checks(self) -> Dict:
        results = {}
        results["database"] = await self._check_database()
        results["llm_providers"] = await self._check_llm_providers()
        results["onepassword"] = await self._check_onepassword()
        results["memory"] = await self._check_memory()
        self._last_results = results

        for component, info in results.items():
            level = logging.INFO if info.get("status") == "healthy" else logging.WARNING
            logger.log(level, "HealthCheck %s: %s", component, info.get("status"))

        await self._alert_on_critical(results)
        return results

    def get_status(self) -> Dict:
        return self._last_results

    async def _check_database(self) -> Dict:
        try:
            from src.database import engine
            from sqlalchemy import text

            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return {
                "status": "healthy",
                "last_check": time.time(),
                "details": "SELECT 1 OK",
            }
        except Exception as e:
            return {
                "status": "down",
                "last_check": time.time(),
                "details": str(e)[:200],
            }

    async def _check_llm_providers(self) -> Dict:
        if not self.circuit_breaker:
            return {
                "status": "healthy",
                "last_check": time.time(),
                "details": "No circuit breaker configured",
            }
        try:
            cb_status = await self.circuit_breaker.get_status()
            open_providers = [
                name
                for name, info in cb_status.items()
                if info.get("state") == "open"
            ]
            if not cb_status:
                status = "healthy"
                detail = "No providers tracked yet"
            elif len(open_providers) == len(cb_status):
                status = "down"
                detail = f"All providers open: {', '.join(open_providers)}"
            elif open_providers:
                status = "degraded"
                detail = f"Open providers: {', '.join(open_providers)}"
            else:
                status = "healthy"
                detail = f"All {len(cb_status)} providers healthy"
            return {
                "status": status,
                "last_check": time.time(),
                "details": detail,
                "providers": cb_status,
            }
        except Exception as e:
            return {
                "status": "degraded",
                "last_check": time.time(),
                "details": str(e)[:200],
            }

    async def _check_onepassword(self) -> Dict:
        try:
            op_exists = shutil.which("op") is not None
            op_token = bool(os.getenv("OP_SERVICE_ACCOUNT_TOKEN") or os.getenv("OP_SESSION"))
            if op_exists and op_token:
                status = "healthy"
                detail = "op binary found, token set"
            elif op_exists:
                status = "degraded"
                detail = "op binary found, no token set"
            else:
                status = "degraded"
                detail = "op binary not found"
            return {
                "status": status,
                "last_check": time.time(),
                "details": detail,
            }
        except Exception as e:
            return {
                "status": "degraded",
                "last_check": time.time(),
                "details": str(e)[:200],
            }

    async def _check_memory(self) -> Dict:
        try:
            try:
                import psutil
                mem = psutil.virtual_memory()
                used_pct = mem.percent
                detail = f"Memory usage: {used_pct}% ({mem.used // (1024*1024)}MB / {mem.total // (1024*1024)}MB)"
            except ImportError:
                with open("/proc/meminfo", "r") as f:
                    lines = f.readlines()
                mem_info = {}
                for line in lines[:5]:
                    parts = line.split(":")
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val = int(parts[1].strip().split()[0])
                        mem_info[key] = val
                total = mem_info.get("MemTotal", 1)
                available = mem_info.get("MemAvailable", total)
                used_pct = round((1 - available / total) * 100, 1)
                detail = f"Memory usage: {used_pct}% ({(total - available) // 1024}MB / {total // 1024}MB)"

            if used_pct > 90:
                status = "down"
            elif used_pct > 75:
                status = "degraded"
            else:
                status = "healthy"
            return {
                "status": status,
                "last_check": time.time(),
                "details": detail,
            }
        except Exception as e:
            return {
                "status": "degraded",
                "last_check": time.time(),
                "details": str(e)[:200],
            }

    async def _alert_on_critical(self, results: Dict) -> None:
        if not self.alert_callback:
            return
        critical_now = set()
        for component, info in results.items():
            if info.get("status") == "down":
                critical_now.add(component)

        new_critical = critical_now - self._previous_critical
        self._previous_critical = critical_now

        for component in new_critical:
            detail = results[component].get("details", "unknown")
            msg = f"ALERT: {component} is DOWN\nDetails: {detail}"
            try:
                await self.alert_callback(msg)
            except Exception:
                logger.exception("Failed to send alert for %s", component)
