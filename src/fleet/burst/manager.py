"""BurstManager — saturation-triggered ephemeral compute scaling.

This module wires the burst backend interface to the existing jurisdiction
ladder, fleet provisioner, and circuit-breaker pressure signals:

  1. SaturationSensor  — reads circuit-breaker state + concurrency pressure
                         from MultiProviderLLM and returns a SaturationSignal.
  2. BurstDecision     — evaluates saturation against the jurisdiction guards
                         (cloud_shift kill switch + exec_locus_pin). Degrades
                         to local/queue instead of provisioning when blocked.
  3. BurstManager      — orchestrates provision → dispatch → drain → teardown
                         with full DB audit trail (BurstWorker + BurstEvent).

Jurisdiction contract (pillar #2 and the kill-switch spec):
  - If cloud_shift=False or exec_locus_pin="local" → burst is BLOCKED; any
    outstanding workers are torn down; no new provisioning happens.
  - The burst path only runs cloud backends (all current adapters are
    provider_hosted or cloud-based). When jurisdiction pins to local, the
    existing local/queue path handles overflow (same as before burst existed).

Audit: every lifecycle transition (provisioned, dispatched, drain_started,
torn_down, burst_blocked) writes a BurstEvent row, so the full burst history
is queryable and surfaced by the fleet status API.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_SATURATION_BURST_THRESHOLD = 0.70   # trigger burst above this pressure (default)
_SATURATION_DRAIN_THRESHOLD = 0.30   # tear down workers below this pressure (default)
_MAX_BURST_WORKERS = 4               # cap concurrent burst workers (default)


# ---------------------------------------------------------------------------
# Config/env helpers — thresholds are operator-tunable via environment.
# Env vars mirror the keys documented in [burst_compute] of providers.toml.
# ---------------------------------------------------------------------------

def _get_burst_threshold() -> float:
    """Return the saturation pressure threshold above which burst is triggered.

    Read from BURST_SATURATION_THRESHOLD env var; falls back to the constant.
    """
    try:
        from src.providers.secrets import get_env
        val = get_env("BURST_SATURATION_THRESHOLD")
        if val:
            return max(0.0, min(1.0, float(val)))
    except Exception:
        pass
    return _SATURATION_BURST_THRESHOLD


def _get_drain_threshold() -> float:
    """Return the saturation pressure threshold below which idle workers are drained.

    Read from BURST_DRAIN_THRESHOLD env var; falls back to the constant.
    """
    try:
        from src.providers.secrets import get_env
        val = get_env("BURST_DRAIN_THRESHOLD")
        if val:
            return max(0.0, min(1.0, float(val)))
    except Exception:
        pass
    return _SATURATION_DRAIN_THRESHOLD


def _get_max_workers() -> int:
    """Return the maximum number of concurrent burst workers.

    Read from BURST_MAX_WORKERS env var; falls back to the constant.
    """
    try:
        from src.providers.secrets import get_env
        val = get_env("BURST_MAX_WORKERS")
        if val:
            return max(1, int(val))
    except Exception:
        pass
    return _MAX_BURST_WORKERS


# ---------------------------------------------------------------------------
# Saturation signal
# ---------------------------------------------------------------------------

@dataclass
class SaturationSignal:
    """Composite saturation pressure reading for the local swarm."""
    circuit_breaker_open_fraction: float = 0.0
    active_call_fraction: float = 0.0
    total_providers: int = 0
    open_providers: int = 0

    @property
    def pressure(self) -> float:
        """Overall saturation pressure in [0, 1].

        The higher of circuit-breaker pressure (providers unavailable) and
        concurrency pressure (active fraction of capacity), bounded to [0, 1].
        """
        return min(1.0, max(self.circuit_breaker_open_fraction, self.active_call_fraction))

    def is_saturated(self, threshold: float = _SATURATION_BURST_THRESHOLD) -> bool:
        return self.pressure >= threshold

    def is_drained(self, threshold: float = _SATURATION_DRAIN_THRESHOLD) -> bool:
        return self.pressure < threshold


async def measure_saturation(llm=None) -> SaturationSignal:
    """Sample current saturation from the shared MultiProviderLLM instance.

    ``llm`` — a MultiProviderLLM instance (injected by the orchestrator).
    When None, attempts to import and construct one; if that fails, returns
    a zero-pressure signal (no saturation detected).
    """
    try:
        if llm is None:
            from src.swarm.llm_multi import MultiProviderLLM
            llm = MultiProviderLLM()

        cb_status = await llm.circuit_breaker.get_status()
        total = len(cb_status)
        if total == 0:
            return SaturationSignal()

        open_count = sum(
            1 for h in cb_status.values() if h.get("state") in ("open", "half_open")
        )
        cb_fraction = open_count / total

        active_fraction = getattr(llm, "_burst_active_fraction", 0.0)

        return SaturationSignal(
            circuit_breaker_open_fraction=cb_fraction,
            active_call_fraction=active_fraction,
            total_providers=total,
            open_providers=open_count,
        )
    except Exception as e:
        logger.debug("measure_saturation failed (%s); returning zero pressure", e)
        return SaturationSignal()


# ---------------------------------------------------------------------------
# Jurisdiction guard for burst
# ---------------------------------------------------------------------------

@dataclass
class BurstDecision:
    allowed: bool
    reason: str
    cloud_shift_active: bool = True
    exec_locus_pin: Optional[str] = None
    worker_provisioned: bool = False  # True only when a new worker was actually provisioned


def evaluate_burst_jurisdiction() -> BurstDecision:
    """Check whether burst provisioning is permitted under current jurisdiction.

    Burst is BLOCKED when:
    - cloud_shift=False (operator disabled cloud egress via kill switch)
    - exec_locus_pin="local" (operator pinned all execution to local tier)

    In both cases the function returns allowed=False with a reason string that
    the manager logs and audits.  No cloud worker is ever provisioned when this
    returns allowed=False.
    """
    try:
        from src.swarm.jurisdiction import get_cloud_shift, get_exec_locus_pin
        cloud_shift = get_cloud_shift()
        pin = get_exec_locus_pin()
    except Exception as e:
        logger.debug("burst jurisdiction check failed (%s); blocking burst as safe default", e)
        return BurstDecision(
            allowed=False,
            reason=f"jurisdiction check error ({e}); defaulting to burst-blocked",
            cloud_shift_active=False,
        )

    if not cloud_shift:
        return BurstDecision(
            allowed=False,
            reason="cloud_shift=false — cloud egress disabled; burst blocked; degrading to local/queue",
            cloud_shift_active=False,
            exec_locus_pin=pin,
        )
    if pin and pin.lower() not in ("any", ""):
        if pin.lower() == "local":
            return BurstDecision(
                allowed=False,
                reason=f"exec_locus_pin='{pin}' — pinned to local; burst blocked",
                cloud_shift_active=True,
                exec_locus_pin=pin,
            )

    return BurstDecision(
        allowed=True,
        reason="jurisdiction permits burst",
        cloud_shift_active=cloud_shift,
        exec_locus_pin=pin,
    )


# ---------------------------------------------------------------------------
# BurstManager
# ---------------------------------------------------------------------------

@dataclass
class ActiveBurstWorker:
    """In-process tracking of a live burst worker."""
    worker_id: str
    db_id: int
    backend_key: str
    handle: Any          # BurstWorkerHandle
    provisioned_at: datetime = field(default_factory=datetime.utcnow)
    dispatches: int = 0


class BurstManager:
    """Manage the full lifecycle of ephemeral burst workers.

    A single shared BurstManager instance is held by the WaveOrchestrator
    (or accessed via get_burst_manager()). It is safe to call from multiple
    coroutines — an asyncio.Lock guards all mutations to _workers.

    Usage:
        manager = get_burst_manager()
        sig = await manager.check_and_burst(llm)   # returns BurstDecision or None
        result = await manager.dispatch_overflow(payload)
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._workers: Dict[str, ActiveBurstWorker] = {}  # worker_id → ActiveBurstWorker

    # ------------------------------------------------------------------
    # Public API used by the orchestrator
    # ------------------------------------------------------------------

    async def check_and_burst(self, llm=None) -> Optional[BurstDecision]:
        """Sample saturation; provision a burst worker if warranted.

        Returns the BurstDecision that was evaluated, or None if saturation
        is below the burst threshold (no action taken).

        ``llm`` should be the live MultiProviderLLM instance from the
        orchestrator so that _burst_active_fraction reflects in-flight calls
        at the moment of sampling.  Call this concurrently with wave execution,
        not before, so concurrency pressure can actually be non-zero.
        """
        sig = await measure_saturation(llm)
        if not sig.is_saturated(_get_burst_threshold()):
            return None

        decision = evaluate_burst_jurisdiction()
        if not decision.allowed:
            await _write_event(
                worker_id="N/A",
                event_type="burst_blocked",
                detail=decision.reason,
            )
            logger.info("BurstManager: burst blocked — %s", decision.reason)
            # Hard-stop: tear down any already-provisioned burst workers immediately.
            # Active cloud workers must not persist when jurisdiction blocks egress.
            if self._workers:
                logger.info(
                    "BurstManager: jurisdiction block — tearing down %d active worker(s)",
                    len(self._workers),
                )
                await self.teardown_all(reason="jurisdiction_blocked")
            return decision

        max_workers = _get_max_workers()
        async with self._lock:
            if len(self._workers) >= max_workers:
                logger.debug(
                    "BurstManager: already at max burst workers (%d); skipping provision",
                    max_workers,
                )
                return decision

        worker = await self._provision_worker(sig, decision)
        decision.worker_provisioned = worker is not None
        return decision

    async def dispatch_overflow(
        self,
        payload: Dict[str, Any],
        prefer_backend: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Dispatch inference payload to a ready burst worker.

        Returns the result dict from the backend, or None if no burst worker
        is available (caller falls back to local queue).
        """
        handle, worker = await self._pick_ready_worker(prefer_backend)
        if handle is None or worker is None:
            return None

        async with self._lock:
            worker.dispatches += 1

        try:
            from src.fleet.burst.registry import get_backend
            backend = get_backend(worker.backend_key)
            if backend is None:
                raise RuntimeError(f"backend '{worker.backend_key}' not found in registry")

            result = await backend.dispatch(handle, payload)
            await _write_event(
                worker_id=worker.worker_id,
                event_type="dispatched",
                detail=f"dispatches={worker.dispatches} ok={result.get('ok')}",
                db_worker_id=worker.db_id,
            )
            return result
        except Exception as e:
            logger.warning("BurstManager: dispatch failed for worker %s: %s", worker.worker_id, e)
            await _write_event(
                worker_id=worker.worker_id,
                event_type="dispatch_failed",
                detail=str(e),
                db_worker_id=worker.db_id,
            )
            return None

    async def drain_if_idle(self, llm=None) -> int:
        """Tear down burst workers when saturation has dropped.

        Returns the number of workers torn down.
        """
        sig = await measure_saturation(llm)
        if not sig.is_drained(_get_drain_threshold()):
            return 0

        async with self._lock:
            to_remove = list(self._workers.values())

        torn = 0
        for worker in to_remove:
            await self._teardown_worker(worker, reason="saturation_drained")
            torn += 1
        return torn

    async def teardown_all(self, reason: str = "manual") -> None:
        """Forcibly tear down all active burst workers."""
        async with self._lock:
            workers = list(self._workers.values())
        for worker in workers:
            await self._teardown_worker(worker, reason=reason)

    def active_worker_count(self) -> int:
        return len(self._workers)

    def worker_summary(self) -> List[Dict[str, Any]]:
        """Snapshot of active workers for the fleet status API."""
        out = []
        for w in self._workers.values():
            out.append({
                "worker_id": w.worker_id,
                "db_id": w.db_id,
                "backend": w.backend_key,
                "dispatches": w.dispatches,
                "provisioned_at": w.provisioned_at.isoformat(),
            })
        return out

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _provision_worker(
        self, sig: SaturationSignal, decision: BurstDecision
    ) -> Optional[ActiveBurstWorker]:
        from src.fleet.burst.registry import first_configured_backend

        backend = first_configured_backend()
        if backend is None:
            logger.info(
                "BurstManager: saturation=%.2f but no burst backend is configured; "
                "degrading to local/queue", sig.pressure
            )
            await _write_event(
                worker_id="N/A",
                event_type="burst_blocked",
                detail=(
                    f"saturation={sig.pressure:.2f} but no configured burst backend found. "
                    "Add MODAL_TOKEN_ID+MODAL_TOKEN_SECRET or RAY_ADDRESS to the vault."
                ),
            )
            return None

        worker_id = str(uuid.uuid4())
        task = {
            "goal": "overflow_inference",
            "domain": "general",
            "saturation_pressure": sig.pressure,
        }

        db_id = await _create_burst_worker_db(
            worker_id=worker_id,
            backend=backend.key,
            config={
                "saturation_pressure": sig.pressure,
                "exec_locus_pin": decision.exec_locus_pin,
            },
        )

        try:
            handle = await backend.provision(worker_id, task)
        except Exception as e:
            logger.warning("BurstManager: provision failed (%s): %s", backend.key, e)
            await _write_event(
                worker_id=worker_id,
                event_type="provision_failed",
                detail=str(e),
                db_worker_id=db_id,
            )
            await _update_burst_worker_db(db_id, status="failed", error=str(e))
            return None

        worker = ActiveBurstWorker(
            worker_id=worker_id,
            db_id=db_id,
            backend_key=backend.key,
            handle=handle,
        )
        async with self._lock:
            self._workers[worker_id] = worker

        await _update_burst_worker_db(db_id, status="active")
        await _write_event(
            worker_id=worker_id,
            event_type="provisioned",
            detail=(
                f"backend={backend.key} saturation={sig.pressure:.2f} "
                f"cloud_shift={decision.cloud_shift_active}"
            ),
            db_worker_id=db_id,
        )
        logger.info(
            "BurstManager: provisioned burst worker %s via %s (saturation=%.2f)",
            worker_id, backend.key, sig.pressure,
        )
        return worker

    async def _pick_ready_worker(
        self, prefer_backend: Optional[str]
    ) -> Tuple[Optional[Any], Optional[ActiveBurstWorker]]:
        async with self._lock:
            workers = list(self._workers.values())

        if prefer_backend:
            workers = [w for w in workers if w.backend_key == prefer_backend] or workers

        for worker in workers:
            try:
                from src.fleet.burst.registry import get_backend
                backend = get_backend(worker.backend_key)
                if backend is None:
                    continue
                status = await backend.status(worker.handle)
                from src.fleet.burst.interface import WorkerState
                if status.state in (WorkerState.READY, WorkerState.BUSY):
                    return worker.handle, worker
            except Exception as e:
                logger.debug("BurstManager: status probe failed for %s: %s", worker.worker_id, e)
        return None, None

    async def _teardown_worker(self, worker: ActiveBurstWorker, reason: str) -> None:
        await _write_event(
            worker_id=worker.worker_id,
            event_type="teardown_started",
            detail=f"reason={reason}",
            db_worker_id=worker.db_id,
        )
        try:
            from src.fleet.burst.registry import get_backend
            backend = get_backend(worker.backend_key)
            if backend:
                await backend.teardown(worker.handle)
        except Exception as e:
            logger.warning(
                "BurstManager: teardown error for worker %s: %s", worker.worker_id, e
            )

        async with self._lock:
            self._workers.pop(worker.worker_id, None)

        await _update_burst_worker_db(worker.db_id, status="terminated", torn_down_at=datetime.utcnow())
        await _write_event(
            worker_id=worker.worker_id,
            event_type="torn_down",
            detail=f"reason={reason}",
            db_worker_id=worker.db_id,
        )
        logger.info("BurstManager: torn down burst worker %s (reason=%s)", worker.worker_id, reason)


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_manager: Optional[BurstManager] = None


def get_burst_manager() -> BurstManager:
    """Return the process-wide BurstManager singleton."""
    global _manager
    if _manager is None:
        _manager = BurstManager()
    return _manager


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _create_burst_worker_db(
    worker_id: str,
    backend: str,
    config: dict,
) -> int:
    """Insert a BurstWorker row and return its id."""
    def _write():
        from src.database import SessionLocal
        from src.models import BurstWorker
        db = SessionLocal()
        try:
            import json
            row = BurstWorker(
                worker_id=worker_id,
                backend=backend,
                status="provisioning",
                config_json=config,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return row.id
        finally:
            db.close()

    try:
        return await asyncio.to_thread(_write)
    except Exception as e:
        logger.warning("BurstManager: could not write BurstWorker row: %s", e)
        return 0


async def _update_burst_worker_db(
    db_id: int,
    status: str,
    error: Optional[str] = None,
    torn_down_at: Optional[datetime] = None,
) -> None:
    if not db_id:
        return

    def _write():
        from src.database import SessionLocal
        from src.models import BurstWorker
        db = SessionLocal()
        try:
            row = db.query(BurstWorker).filter(BurstWorker.id == db_id).first()
            if row:
                row.status = status
                if error is not None:
                    row.error = error
                if torn_down_at is not None:
                    row.torn_down_at = torn_down_at
                db.commit()
        finally:
            db.close()

    try:
        await asyncio.to_thread(_write)
    except Exception as e:
        logger.debug("BurstManager: BurstWorker update failed: %s", e)


async def _write_event(
    worker_id: str,
    event_type: str,
    detail: str = "",
    db_worker_id: int = 0,
) -> None:
    def _write():
        from src.database import SessionLocal
        from src.models import BurstEvent
        db = SessionLocal()
        try:
            ev = BurstEvent(
                worker_id=worker_id,
                db_worker_id=db_worker_id or None,
                event_type=event_type,
                detail=detail[:2000],
            )
            db.add(ev)
            db.commit()
        finally:
            db.close()

    try:
        await asyncio.to_thread(_write)
    except Exception as e:
        logger.debug("BurstManager: BurstEvent write failed: %s", e)
