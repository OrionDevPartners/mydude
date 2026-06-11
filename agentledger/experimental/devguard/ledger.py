"""DevGuard audit ledger + quarantine.

Persists guardian / repair *outcomes* to the durable Postgres episodic ledger
(``agent_memory.episodic_ledger`` via the existing
:class:`agentledger.experimental.memory_manager.LedgerStore`) and quarantines a
misbehaving *subject* (a repair target, capability key, etc.) once it racks up
repeated failures — by reusing MyDude's existing async
:class:`src.selfheal.circuit_breaker.CircuitBreaker`. Nothing is re-implemented:
the audit log and the breaker are the project's own components.

This module touches real resources (Postgres), so it is behind the production
gate. The breaker is async; DevGuard's surface is sync, so its coroutines are
driven on a private event loop. Call the sync methods from sync code; from an
already-running loop, drive ``self.breaker`` directly.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Optional

from src.selfheal.circuit_breaker import CircuitBreaker

from ..gate import require_enabled
from ..memory_manager import LedgerStore

KIND_SUCCESS = "guardian.success"
KIND_FAILURE = "guardian.failure"
KIND_ASSESSMENT = "guardian.assessment"


def _as_dict(obj: Any) -> Any:
    return obj.to_dict() if hasattr(obj, "to_dict") else obj


class GuardianLedger:
    """Append-only outcome audit + circuit-breaker quarantine for DevGuard."""

    def __init__(
        self,
        *,
        ledger: LedgerStore,
        breaker: Optional[CircuitBreaker] = None,
        session_id: str = "devguard",
        failure_threshold: int = 3,
        recovery_timeout: float = 300.0,
    ) -> None:
        self.ledger = ledger
        self.breaker = breaker or CircuitBreaker(
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
        )
        self.session_id = session_id
        self._loop = asyncio.new_event_loop()
        self._owns_pool = None

    # -- constructors ---------------------------------------------------- #
    @classmethod
    def from_dsn(
        cls,
        dsn: Optional[str] = None,
        *,
        schema: str = "agent_memory",
        force: bool = False,
        **kwargs: Any,
    ) -> "GuardianLedger":
        """Build a self-contained ledger over its own psycopg pool."""
        require_enabled(force=force)
        dsn = dsn or os.environ.get("DATABASE_URL")
        if not dsn:
            raise RuntimeError(
                "DATABASE_URL is not set; the DevGuard audit ledger requires PostgreSQL."
            )
        from psycopg_pool import ConnectionPool

        pool = ConnectionPool(
            conninfo=dsn,
            min_size=1,
            max_size=4,
            open=True,
            kwargs={"connect_timeout": 15},
        )
        store = LedgerStore(pool, schema)
        store.ensure_schema()
        inst = cls(ledger=store, **kwargs)
        inst._owns_pool = pool
        return inst

    @classmethod
    def from_memory_manager(cls, mem: Any, **kwargs: Any) -> "GuardianLedger":
        """Reuse a connected MemoryManager's LedgerStore (no new pool)."""
        if getattr(mem, "ledger", None) is None:
            raise RuntimeError("MemoryManager is not connected; call .connect() first.")
        return cls(ledger=mem.ledger, **kwargs)

    # -- async bridge ---------------------------------------------------- #
    def _run(self, coro):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return self._loop.run_until_complete(coro)
        raise RuntimeError(
            "GuardianLedger sync methods cannot be called from a running event "
            "loop; drive self.breaker (async) directly instead."
        )

    # -- queries --------------------------------------------------------- #
    def subject_state(self, subject: str) -> dict[str, Any]:
        status = self._run(self.breaker.get_status())
        h = status.get(subject) or {}
        state = h.get("state", "closed")
        return {
            "subject": subject,
            "state": state,
            "failure_count": h.get("failure_count", 0),
            "success_count": h.get("success_count", 0),
            "quarantined": state == "open",
        }

    def can_attempt(self, subject: str) -> bool:
        """True if the subject is allowed another attempt (breaker not open)."""
        return self._run(self.breaker.can_call(subject))

    def is_quarantined(self, subject: str) -> bool:
        return not self.can_attempt(subject)

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.ledger.history(self.session_id, limit=limit)

    # -- writes ---------------------------------------------------------- #
    def record_assessment(
        self,
        subject: str,
        assessment: Any,
        *,
        actor: str = "devguard.guardian",
    ) -> int:
        """Persist a guardian tier decision (no success/failure side effect)."""
        payload = {"subject": subject, "assessment": _as_dict(assessment)}
        return self.ledger.append(
            self.session_id, KIND_ASSESSMENT, payload=payload, actor=actor
        )

    def record_outcome(
        self,
        subject: str,
        *,
        success: bool,
        assessment: Any = None,
        detail: Optional[str] = None,
        error: Optional[str] = None,
        actor: str = "devguard.guardian",
    ) -> dict[str, Any]:
        """Record a repair outcome: update the breaker, then persist the event."""
        if success:
            self._run(self.breaker.record_success(subject))
        else:
            self._run(
                self.breaker.record_failure(
                    subject, error or detail or "guardian repair failed"
                )
            )
        state = self.subject_state(subject)
        payload: dict[str, Any] = {
            "subject": subject,
            "success": bool(success),
            "breaker_state": state["state"],
            "failure_count": state["failure_count"],
            "quarantined": state["quarantined"],
        }
        if assessment is not None:
            payload["assessment"] = _as_dict(assessment)
        if detail:
            payload["detail"] = detail
        if error:
            payload["error"] = str(error)
        event_id = self.ledger.append(
            self.session_id,
            KIND_SUCCESS if success else KIND_FAILURE,
            payload=payload,
            actor=actor,
        )
        return {"event_id": event_id, **state}

    # -- lifecycle ------------------------------------------------------- #
    def close(self) -> None:
        if not self._loop.is_closed():
            self._loop.close()
        if self._owns_pool is not None:
            self._owns_pool.close()
            self._owns_pool = None

    def __enter__(self) -> "GuardianLedger":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
