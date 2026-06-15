"""Provider-agnostic burst-backend interface.

Every burst backend (Modal, Ray, boto3/EC2, etc.) implements BurstBackend.
The BurstManager calls ONLY these four methods; adding a new backend is a
one-file adapter — no call-site changes.

Lifecycle contract:
  provision()  — allocate an ephemeral compute slot / worker; returns a
                 BurstWorkerHandle that carries the backend-assigned worker_id
                 and whatever routing state the backend needs for dispatch.
  dispatch()   — submit inference payload to a provisioned worker and await the
                 result dict.  May be called multiple times on the same handle.
  status()     — query the live worker state without side effects.
  teardown()   — release the worker and free its resources.  MUST be idempotent:
                 calling it on an already-torn-down worker must not raise.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class WorkerState(str, Enum):
    PENDING = "pending"       # provisioning in progress
    READY = "ready"           # running, accepting dispatch
    BUSY = "busy"             # dispatch in-flight
    DRAINING = "draining"     # teardown requested; finishing last dispatch
    TERMINATED = "terminated" # destroyed / cleaned up
    FAILED = "failed"         # irrecoverable error


@dataclass
class BurstWorkerHandle:
    """Opaque descriptor returned by provision() and passed to dispatch/status/teardown.

    ``worker_id``    — stable identifier across restart / retry (deterministic,
                       unique per burst request — manager assigns it).
    ``backend_ref``  — backend-specific routing info (function URL, task ARN,
                       Ray job ID, …).  Opaque to the manager.
    ``backend_key``  — which backend produced this handle (for routing back on
                       status / teardown when the manager holds a registry).
    ``metadata``     — cost estimate, region, image tag, etc. — surfaced in the
                       audit trail but never used for routing logic.
    """
    worker_id: str
    backend_key: str
    backend_ref: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BurstWorkerStatus:
    worker_id: str
    state: WorkerState
    backend_key: str
    detail: str = ""
    cost_so_far: Optional[float] = None


class BurstBackend(ABC):
    """Abstract burst-backend interface.

    Concrete implementations live in src/fleet/burst/backends/.
    They are registered in config/providers.toml [burst_compute] and resolved
    by src/fleet/burst/registry.py.

    ALL methods must:
    - be async (even if the underlying SDK is sync — wrap with asyncio.to_thread)
    - fail loud on misconfiguration (raise, never silently pretend)
    - never touch secrets directly — resolve via the connector proxy / vault
    """

    @property
    @abstractmethod
    def key(self) -> str:
        """Unique identifier matching the [burstbackends.<key>] config block."""

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True only when this backend can actually provision a worker.

        Checks credentials, SDK availability, etc.  Called before provisioning
        so the manager can skip unconfigured backends without attempting a round
        trip.  Must not raise.
        """

    @abstractmethod
    async def provision(
        self,
        worker_id: str,
        task: Dict[str, Any],
    ) -> BurstWorkerHandle:
        """Allocate an ephemeral compute worker.

        ``worker_id`` — stable identifier chosen by the manager (UUID4).
        ``task``       — descriptor: {"goal": ..., "domain": ..., "budget_tokens": ...}

        Raises RuntimeError (or a subclass) on provisioning failure — the
        manager catches it, records a burst_event, and degrades to local/queue.
        Must NOT silently return a fake handle.
        """

    @abstractmethod
    async def dispatch(
        self,
        handle: BurstWorkerHandle,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Submit inference payload to a provisioned worker and return the result.

        ``payload`` — {"system": str, "user": str, "budget_tokens": int, ...}
        Return dict must contain at least {"ok": bool, "text": str}.
        Raises on transport error.  The manager records the event regardless.
        """

    @abstractmethod
    async def status(self, handle: BurstWorkerHandle) -> BurstWorkerStatus:
        """Query the live worker state.  Never raises — return FAILED on error."""

    @abstractmethod
    async def teardown(self, handle: BurstWorkerHandle) -> None:
        """Release the worker and free cloud resources.

        MUST be idempotent: calling on an already-torn-down worker must succeed
        silently (log at DEBUG if needed, but never raise).
        """
