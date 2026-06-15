"""Ray burst backend adapter.

Provisions Ray remote tasks for overflow inference on a running Ray cluster.
This is a DROP-IN SIBLING to modal_adapter.py — it demonstrates the one-file
adapter pattern: only this file changes when adding Ray support.

Credentials / configuration:
  RAY_ADDRESS        — cluster address, e.g. ray://localhost:10001 or
                       ray://auto (auto-detect local cluster)
  RAY_BURST_TOKEN    — optional dashboard / head-node auth token

If RAY_ADDRESS is not set, is_configured() returns False and provision()
raises RuntimeError immediately (fail loud, pillar #1).

The Ray backend dispatches inference by submitting a remote task that calls
back into the local LLM provider stack (or a remote inference endpoint).
For cloud-deployed MyDude, the Ray cluster workers must have provider
credentials available (mounted secrets or vault access).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from src.fleet.burst.interface import (
    BurstBackend,
    BurstWorkerHandle,
    BurstWorkerStatus,
    WorkerState,
)

logger = logging.getLogger(__name__)

_KEY = "ray"


class RayBurstBackend(BurstBackend):
    """Ray remote-task burst backend.

    Each provisioned worker corresponds to a Ray remote task submitted to the
    cluster at RAY_ADDRESS.  The Ray SDK (``ray``) must be installed and the
    cluster must be reachable.  If either condition is not met, provision()
    raises so the BurstManager can degrade to local/queue rather than silently
    doing nothing.
    """

    @property
    def key(self) -> str:
        return _KEY

    def is_configured(self) -> bool:
        from src.providers.secrets import get_env
        return bool(get_env("RAY_ADDRESS"))

    def _ray_address(self) -> str:
        from src.providers.secrets import get_env
        addr = get_env("RAY_ADDRESS") or ""
        if not addr:
            raise RuntimeError(
                f"Burst backend '{_KEY}': RAY_ADDRESS is not set. "
                "Point it at a running Ray cluster (e.g. ray://localhost:10001) "
                "before using the Ray burst backend."
            )
        return addr

    def _import_ray(self):
        try:
            import ray  # noqa: F401
            return ray
        except ImportError:
            raise RuntimeError(
                f"Burst backend '{_KEY}': the 'ray' package is not installed. "
                "Add it to pyproject.toml and re-lock dependencies before using "
                "the Ray burst backend."
            )

    async def provision(
        self, worker_id: str, task: Dict[str, Any]
    ) -> BurstWorkerHandle:
        ray = self._import_ray()
        address = self._ray_address()

        def _connect():
            if not ray.is_initialized():
                ray.init(address=address, ignore_reinit_error=True)

        await asyncio.to_thread(_connect)

        handle = BurstWorkerHandle(
            worker_id=worker_id,
            backend_key=_KEY,
            backend_ref=address,
            metadata={
                "ray_address": address,
                "goal_preview": str(task.get("goal", ""))[:200],
                "domain": task.get("domain", "general"),
            },
        )
        logger.info("Ray burst: provisioned worker %s → %s", worker_id, address)
        return handle

    async def dispatch(
        self, handle: BurstWorkerHandle, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        ray = self._import_ray()

        @ray.remote
        def _inference_task(payload_inner):
            import asyncio as _asyncio
            from src.swarm.llm_multi import MultiProviderLLM

            llm = MultiProviderLLM()
            result = _asyncio.get_event_loop().run_until_complete(
                llm.call_team(
                    system=payload_inner.get("system", ""),
                    user=payload_inner.get("user", ""),
                    domain=payload_inner.get("domain", "general"),
                )
            )
            return result.get("merged", "")

        def _submit():
            ref = _inference_task.remote(payload)
            return ray.get(ref, timeout=payload.get("timeout_sec", 120))

        try:
            text = await asyncio.to_thread(_submit)
            logger.info("Ray burst: worker %s dispatch ok", handle.worker_id)
            return {"ok": True, "text": text or ""}
        except Exception as e:
            raise RuntimeError(
                f"Ray burst dispatch failed for worker {handle.worker_id}: {e}"
            ) from e

    async def status(self, handle: BurstWorkerHandle) -> BurstWorkerStatus:
        ray = None
        try:
            ray = self._import_ray()
        except RuntimeError as e:
            return BurstWorkerStatus(
                worker_id=handle.worker_id,
                state=WorkerState.FAILED,
                backend_key=_KEY,
                detail=str(e),
            )
        try:
            def _check():
                return ray.is_initialized()
            initialized = await asyncio.to_thread(_check)
            state = WorkerState.READY if initialized else WorkerState.PENDING
            return BurstWorkerStatus(
                worker_id=handle.worker_id,
                state=state,
                backend_key=_KEY,
                detail=f"ray_address={handle.metadata.get('ray_address', '')}",
            )
        except Exception as e:
            return BurstWorkerStatus(
                worker_id=handle.worker_id,
                state=WorkerState.FAILED,
                backend_key=_KEY,
                detail=str(e),
            )

    async def teardown(self, handle: BurstWorkerHandle) -> None:
        logger.debug(
            "Ray burst: teardown worker %s — Ray tasks are ephemeral; "
            "connection remains open for the cluster lifetime.", handle.worker_id
        )
