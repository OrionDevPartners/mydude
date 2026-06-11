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
        # Per local-provider last-seen reachability (True=up, False=down). Used to
        # raise a SentinelEvent only on the up->down transition, not every tick.
        self._previous_local_status: Dict[str, Optional[bool]] = {}

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
        results["local_nodes"] = await self._check_local_nodes()
        results["onepassword"] = await self._check_onepassword()
        results["memory"] = await self._check_memory()
        self._last_results = results

        for component, info in results.items():
            level = logging.INFO if info.get("status") == "healthy" else logging.WARNING
            logger.log(level, "HealthCheck %s: %s", component, info.get("status"))

        await self._alert_on_local_offline(results["local_nodes"])
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

    async def _check_local_nodes(self) -> Dict:
        """Probe Mesh-connected local model nodes (exec_locus=local) for reachability.

        Reuses ``provider_exec_locus_distribution`` (which performs the live TCP
        probe). Only providers with a configured endpoint are considered "nodes" —
        a local provider with no endpoint configured is not a node we can monitor.
        """
        try:
            from src.swarm.jurisdiction import provider_exec_locus_distribution
            dist = await asyncio.to_thread(provider_exec_locus_distribution)
        except Exception as e:
            return {
                "status": "degraded",
                "last_check": time.time(),
                "details": str(e)[:200],
                "nodes": [],
            }

        nodes = [
            p for p in dist
            if p.get("exec_locus") == "local" and p.get("endpoint")
        ]
        down = [n for n in nodes if n.get("server_up") is False]
        up = [n for n in nodes if n.get("server_up") is True]

        if not nodes:
            status = "healthy"
            detail = "No Mesh-connected local nodes configured"
        elif down and not up:
            status = "down"
            detail = "All local nodes offline: " + ", ".join(
                n.get("provider", "?") for n in down
            )
        elif down:
            status = "degraded"
            detail = "Local nodes offline: " + ", ".join(
                n.get("provider", "?") for n in down
            )
        else:
            status = "healthy"
            detail = f"All {len(nodes)} local node(s) reachable"

        return {
            "status": status,
            "last_check": time.time(),
            "details": detail,
            "nodes": nodes,
        }

    async def _alert_on_local_offline(self, info: Dict) -> None:
        """Raise a SentinelEvent when a local node flips from up to down.

        State is tracked per provider so an event fires once on the transition
        (and again only after the node recovers and drops again), never on every
        tick while it stays offline.
        """
        nodes = info.get("nodes") or []
        up_count = sum(1 for n in nodes if n.get("server_up") is True)

        for node in nodes:
            key = node.get("provider")
            if not key:
                continue
            server_up = node.get("server_up")
            if server_up is None:
                # Probe skipped (no resolvable endpoint) — nothing to compare.
                continue

            prev = self._previous_local_status.get(key)
            was_down = prev is False
            now_down = server_up is False
            self._previous_local_status[key] = server_up

            if now_down and not was_down:
                # Last reachable local node going dark is high severity because the
                # swarm now has no local fallback; otherwise medium.
                severity = "high" if up_count == 0 else "medium"
                await self._raise_local_offline_event(node, severity)
            elif (not now_down) and was_down:
                logger.info(
                    "Local node %s back online at %s",
                    key,
                    node.get("endpoint") or "unknown",
                )

    async def _raise_local_offline_event(self, node: Dict, severity: str) -> None:
        provider = node.get("provider", "unknown")
        endpoint = node.get("endpoint") or "unknown"
        # Stable per-node alert id so we can dedup against an already-open alert.
        alert_id = f"LOCAL-OFFLINE-{provider}"
        try:
            raised = await asyncio.to_thread(
                self._record_local_offline_if_absent, alert_id, severity, provider, endpoint
            )
            if raised:
                logger.warning(
                    "SentinelEvent raised: local node %s offline at %s (sev=%s)",
                    provider, endpoint, severity,
                )
        except Exception:
            logger.exception("Failed to raise local-node-offline event for %s", provider)

    @staticmethod
    def _record_local_offline_if_absent(
        alert_id: str, severity: str, provider: str, endpoint: str
    ) -> bool:
        """Persist a local-node-offline SentinelEvent unless one is already open.

        Dedup is keyed on the stable per-node ``alert_id`` and only suppresses
        rows that are still unacknowledged — once an operator acks (after the node
        recovers) a subsequent drop raises a fresh alert. Returns True if a new
        event was written. This also stops process restarts from piling up
        duplicate open alerts for a node that was already down.
        """
        from src.database import SessionLocal
        from src.models import SentinelEvent
        from src.swarm.error_metrics import record_sentinel_event

        db = SessionLocal()
        try:
            existing = (
                db.query(SentinelEvent)
                .filter(
                    SentinelEvent.alert_id == alert_id,
                    SentinelEvent.acknowledged == False,  # noqa: E712
                )
                .first()
            )
            if existing:
                return False
        finally:
            db.close()

        record_sentinel_event(
            "local_node_offline",
            severity,
            (
                f"Mesh-connected local model node '{provider}' is unreachable "
                f"at {endpoint}. Tasks are silently falling back to cloud."
            ),
            (
                f"Check the {provider} node and its Cloudflare Mesh link. "
                f"Restore the endpoint at {endpoint}, then acknowledge this alert."
            ),
            alert_id=alert_id,
        )
        return True

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


_monitor: Optional[HealthMonitor] = None


def get_health_monitor() -> HealthMonitor:
    """Process-wide singleton background HealthMonitor.

    Used by the app startup chain to run periodic health checks (including the
    Mesh-connected local-node probe that raises SentinelEvents) without the
    operator having to load /system.
    """
    global _monitor
    if _monitor is None:
        from src.selfheal.circuit_breaker import CircuitBreaker
        _monitor = HealthMonitor(circuit_breaker=CircuitBreaker())
    return _monitor
