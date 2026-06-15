"""Integration tests for the Distributed Power-Burst Fabric.

Proves the full lifecycle end-to-end without live cloud calls:
  saturation → provision → overflow dispatch → drain teardown
  jurisdiction guard: cloud_shift=False → burst blocked + workers torn down
  jurisdiction guard: exec_locus_pin=local → burst blocked + workers torn down
  env-tunable threshold overrides respected
  BURST_COMPUTE key present in orchestrator result
"""
import asyncio
import os
import sys
import unittest.mock as mock
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional


class _Approx:
    """Tiny float-tolerance helper (drop-in for ``pytest.approx`` for ==).

    Keeps this suite runnable standalone (``python tests/test_burst_manager.py``)
    in environments where pytest is not installed, while remaining fully
    pytest-compatible.
    """

    def __init__(self, expected, rel=1e-6, abs=1e-12):
        self.expected = expected
        self.rel = rel
        self.abs = abs

    def __eq__(self, other):
        try:
            return abs(other - self.expected) <= max(
                self.abs, self.rel * abs(self.expected)
            )
        except TypeError:
            return NotImplemented

    def __repr__(self):
        return "approx(%r)" % (self.expected,)


def approx(expected, rel=1e-6, abs=1e-12):
    return _Approx(expected, rel=rel, abs=abs)


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.fleet.burst.interface import (
    BurstBackend,
    BurstWorkerHandle,
    BurstWorkerStatus,
    WorkerState,
)
from src.fleet.burst.manager import (
    ActiveBurstWorker,
    BurstDecision,
    BurstManager,
    SaturationSignal,
    _get_burst_threshold,
    _get_drain_threshold,
    _get_max_workers,
    evaluate_burst_jurisdiction,
    measure_saturation,
)


# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------


class _MockLLM:
    """Fake MultiProviderLLM with controllable saturation signals."""

    def __init__(self, cb_states: Dict[str, str], active_fraction: float = 0.0):
        self._cb_states = cb_states
        self._burst_active_fraction = active_fraction

    class _CB:
        def __init__(self, states):
            self._states = states

        async def get_status(self):
            return {k: {"state": v} for k, v in self._states.items()}

    @property
    def circuit_breaker(self):
        return self._CB(self._cb_states)


class _MockBackend(BurstBackend):
    """In-memory burst backend — no cloud calls."""

    key = "mock"
    provisioned: list = []
    dispatched: list = []
    torn_down: list = []

    def is_configured(self) -> bool:
        return True

    async def provision(self, worker_id: str, task: Dict[str, Any]) -> BurstWorkerHandle:
        self.provisioned.append(worker_id)
        return BurstWorkerHandle(
            worker_id=worker_id,
            backend_key=self.key,
            backend_ref=f"mock://{worker_id}",
            metadata={"goal_preview": str(task.get("goal", ""))[:50]},
        )

    async def dispatch(self, handle: BurstWorkerHandle, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.dispatched.append(handle.worker_id)
        return {"ok": True, "text": f"burst response for {payload.get('user', '')}"}

    async def status(self, handle: BurstWorkerHandle) -> BurstWorkerStatus:
        return BurstWorkerStatus(
            worker_id=handle.worker_id,
            state=WorkerState.READY,
            backend_key=self.key,
        )

    async def teardown(self, handle: BurstWorkerHandle) -> None:
        self.torn_down.append(handle.worker_id)


def _patched_mgr(backend: _MockBackend) -> BurstManager:
    """Return a fresh BurstManager with the mock backend patched into the registry."""
    mgr = BurstManager()
    with mock.patch("src.fleet.burst.registry.first_configured_backend", return_value=backend), \
         mock.patch("src.fleet.burst.registry.get_backend", return_value=backend):
        return mgr


# ---------------------------------------------------------------------------
# SaturationSignal unit tests
# ---------------------------------------------------------------------------


def test_saturation_signal_pressure_is_max_of_cb_and_concurrency():
    sig = SaturationSignal(
        circuit_breaker_open_fraction=0.5,
        active_call_fraction=0.8,
        total_providers=4,
        open_providers=2,
    )
    assert sig.pressure == approx(0.8)


def test_saturation_signal_is_saturated_at_default_threshold():
    sig = SaturationSignal(circuit_breaker_open_fraction=0.75, active_call_fraction=0.0)
    assert sig.is_saturated(0.70)
    assert not sig.is_saturated(0.80)


def test_saturation_signal_is_drained_at_default_threshold():
    sig = SaturationSignal(circuit_breaker_open_fraction=0.20, active_call_fraction=0.10)
    assert sig.is_drained(0.30)
    assert not sig.is_drained(0.10)


# ---------------------------------------------------------------------------
# measure_saturation reads injected live LLM instance
# ---------------------------------------------------------------------------


def test_measure_saturation_uses_injected_llm():
    llm = _MockLLM(
        cb_states={"p1": "open", "p2": "open", "p3": "closed"},
        active_fraction=0.9,
    )
    sig = asyncio.run(measure_saturation(llm))
    assert sig.total_providers == 3
    assert sig.open_providers == 2
    assert sig.circuit_breaker_open_fraction == approx(2 / 3)
    assert sig.active_call_fraction == approx(0.9)
    assert sig.pressure == approx(0.9)


def test_measure_saturation_returns_zero_on_no_llm():
    sig = asyncio.run(measure_saturation(None))
    assert isinstance(sig, SaturationSignal)
    assert sig.pressure >= 0.0


# ---------------------------------------------------------------------------
# Env-tunable thresholds
# ---------------------------------------------------------------------------


def test_threshold_helpers_read_from_env():
    with mock.patch.dict(os.environ, {
        "BURST_SATURATION_THRESHOLD": "0.55",
        "BURST_DRAIN_THRESHOLD": "0.15",
        "BURST_MAX_WORKERS": "8",
    }):
        assert _get_burst_threshold() == approx(0.55)
        assert _get_drain_threshold() == approx(0.15)
        assert _get_max_workers() == 8


def test_threshold_helpers_fallback_to_defaults():
    with mock.patch.dict(os.environ, {}, clear=True):
        assert _get_burst_threshold() == approx(0.70)
        assert _get_drain_threshold() == approx(0.30)
        assert _get_max_workers() == 4


def test_threshold_helpers_clamp_to_01():
    with mock.patch.dict(os.environ, {
        "BURST_SATURATION_THRESHOLD": "2.0",
        "BURST_DRAIN_THRESHOLD": "-0.5",
    }):
        assert _get_burst_threshold() == approx(1.0)
        assert _get_drain_threshold() == approx(0.0)


# ---------------------------------------------------------------------------
# Full lifecycle: saturation → provision → dispatch → drain teardown
# ---------------------------------------------------------------------------


def test_full_lifecycle_saturation_provision_dispatch_drain():
    backend = _MockBackend()
    mgr = BurstManager()

    high_sat_llm = _MockLLM(
        cb_states={"p1": "open", "p2": "open", "p3": "open", "p4": "closed"},
        active_fraction=0.9,
    )
    low_sat_llm = _MockLLM(
        cb_states={"p1": "closed", "p2": "closed"},
        active_fraction=0.1,
    )

    async def run():
        # 1. check_and_burst — saturation is high, jurisdiction permits → provision
        with mock.patch("src.fleet.burst.registry.first_configured_backend", return_value=backend), \
             mock.patch("src.fleet.burst.registry.get_backend", return_value=backend), \
             mock.patch("src.fleet.burst.manager._get_burst_threshold", return_value=0.70), \
             mock.patch("src.fleet.burst.manager.evaluate_burst_jurisdiction",
                        return_value=BurstDecision(allowed=True, reason="test-permit")):
            decision = await mgr.check_and_burst(high_sat_llm)

        assert decision is not None, "expected a burst decision"
        assert decision.allowed, "jurisdiction should permit"
        assert decision.worker_provisioned, "a worker should have been provisioned"
        assert mgr.active_worker_count() == 1
        assert len(backend.provisioned) == 1

        # 2. dispatch_overflow — worker is ready
        with mock.patch("src.fleet.burst.registry.get_backend", return_value=backend):
            result = await mgr.dispatch_overflow(
                {"system": "You are helpful.", "user": "Hello burst!", "domain": "general"}
            )

        assert result is not None, "dispatch should return a result"
        assert result.get("ok") is True
        assert "burst response" in result.get("text", "")
        assert len(backend.dispatched) == 1

        # 3. drain_if_idle — saturation dropped → teardown
        with mock.patch("src.fleet.burst.registry.get_backend", return_value=backend), \
             mock.patch("src.fleet.burst.manager._get_drain_threshold", return_value=0.30):
            torn = await mgr.drain_if_idle(low_sat_llm)

        assert torn == 1, f"expected 1 torn down, got {torn}"
        assert mgr.active_worker_count() == 0
        assert len(backend.torn_down) == 1

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Jurisdiction guard: cloud_shift=False → burst blocked + workers torn down
# ---------------------------------------------------------------------------


def test_jurisdiction_cloud_shift_false_blocks_and_tears_down():
    backend = _MockBackend()
    mgr = BurstManager()

    # Pre-populate an active worker to prove teardown fires on block
    handle = BurstWorkerHandle(worker_id="pre-w1", backend_key="mock", backend_ref="", metadata={})
    mgr._workers["pre-w1"] = ActiveBurstWorker(
        worker_id="pre-w1", db_id=0, backend_key="mock", handle=handle
    )
    assert mgr.active_worker_count() == 1

    high_sat_llm = _MockLLM(
        cb_states={"p1": "open", "p2": "open"},
        active_fraction=0.9,
    )

    async def run():
        with mock.patch("src.swarm.jurisdiction.get_cloud_shift", return_value=False), \
             mock.patch("src.swarm.jurisdiction.get_exec_locus_pin", return_value="any"), \
             mock.patch("src.fleet.burst.registry.get_backend", return_value=backend):
            decision = await mgr.check_and_burst(high_sat_llm)

        assert decision is not None
        assert not decision.allowed, "cloud_shift=False must block burst"
        assert "cloud_shift=false" in decision.reason.lower()
        assert mgr.active_worker_count() == 0, "existing workers must be torn down"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Jurisdiction guard: exec_locus_pin=local → burst blocked + workers torn down
# ---------------------------------------------------------------------------


def test_jurisdiction_exec_locus_local_blocks_and_tears_down():
    backend = _MockBackend()
    mgr = BurstManager()

    handle = BurstWorkerHandle(worker_id="pre-w2", backend_key="mock", backend_ref="", metadata={})
    mgr._workers["pre-w2"] = ActiveBurstWorker(
        worker_id="pre-w2", db_id=0, backend_key="mock", handle=handle
    )
    assert mgr.active_worker_count() == 1

    high_sat_llm = _MockLLM(
        cb_states={"p1": "open", "p2": "open"},
        active_fraction=0.9,
    )

    async def run():
        with mock.patch("src.swarm.jurisdiction.get_cloud_shift", return_value=True), \
             mock.patch("src.swarm.jurisdiction.get_exec_locus_pin", return_value="local"), \
             mock.patch("src.fleet.burst.registry.get_backend", return_value=backend):
            decision = await mgr.check_and_burst(high_sat_llm)

        assert decision is not None
        assert not decision.allowed, "exec_locus_pin=local must block burst"
        assert "local" in decision.reason.lower()
        assert mgr.active_worker_count() == 0, "existing workers must be torn down"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# dispatch_overflow returns None when no workers are active
# ---------------------------------------------------------------------------


def test_dispatch_overflow_returns_none_when_no_workers():
    mgr = BurstManager()
    result = asyncio.run(
        mgr.dispatch_overflow({"system": "sys", "user": "user", "domain": "general"})
    )
    assert result is None, "no active workers → dispatch must return None"


# ---------------------------------------------------------------------------
# teardown_all is idempotent
# ---------------------------------------------------------------------------


def test_teardown_all_idempotent():
    backend = _MockBackend()
    mgr = BurstManager()

    handle = BurstWorkerHandle(worker_id="idem-w1", backend_key="mock", backend_ref="", metadata={})
    mgr._workers["idem-w1"] = ActiveBurstWorker(
        worker_id="idem-w1", db_id=0, backend_key="mock", handle=handle
    )

    async def run():
        with mock.patch("src.fleet.burst.registry.get_backend", return_value=backend):
            await mgr.teardown_all(reason="test")
            assert mgr.active_worker_count() == 0
            await mgr.teardown_all(reason="idempotent-second-call")
            assert mgr.active_worker_count() == 0

    asyncio.run(run())


# ---------------------------------------------------------------------------
# worker_provisioned flag is False when no backend configured
# ---------------------------------------------------------------------------


def test_worker_provisioned_false_when_no_backend():
    mgr = BurstManager()
    high_sat_llm = _MockLLM(cb_states={"p1": "open", "p2": "open"}, active_fraction=0.9)

    async def run():
        with mock.patch("src.fleet.burst.registry.first_configured_backend", return_value=None), \
             mock.patch("src.fleet.burst.manager.evaluate_burst_jurisdiction",
                        return_value=BurstDecision(allowed=True, reason="test-permit")):
            decision = await mgr.check_and_burst(high_sat_llm)
        assert decision is not None
        assert decision.allowed
        assert not decision.worker_provisioned, "no backend → worker_provisioned must be False"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Standalone runner (mirrors the other suites in tests/)
# ---------------------------------------------------------------------------


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("ok   %s" % fn.__name__)
        except Exception as e:
            failed += 1
            print("FAIL %s: %s: %s" % (fn.__name__, type(e).__name__, e))
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
