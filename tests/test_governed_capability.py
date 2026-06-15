"""Tests for governed non-LLM capability invocation (governance pillar #4).

Coverage:
  - governed_call enforces the container_compute command allow-list
    (disallowed / destructive commands are rejected AND audited).
  - governed_call rejects an out-of-jurisdiction call (no permitted provider)
    and records the rejection.
  - governed_call records exec_locus + caller identity on success.
  - PolicyEngine.evaluate_compute_command shares the SSH allow-list, accepting
    both string and argv-list commands.
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from src.capabilities.adapters.container_compute import SubprocessComputeAdapter
from src.capabilities.base import CapabilitySpec
from src.capabilities.resolver import (
    CapabilityDenied,
    CapabilityNotAvailable,
    governed_call,
    governed_call_async,
)


def _compute_adapter():
    return SubprocessComputeAdapter(
        CapabilitySpec(
            key="subprocess_local", adapter="subprocess_local",
            category="container_compute", secrets=[], exec_locus="local",
        )
    )


class _FakeResolver:
    """Stand-in resolver: returns a fixed adapter, or raises to simulate an
    out-of-jurisdiction / unavailable category."""

    def __init__(self, adapter=None, raises=None):
        self._adapter = adapter
        self._raises = raises

    def resolve(self, category, *, exec_locus_pin=None, cloud_shift=None):
        if self._raises is not None:
            raise self._raises
        return self._adapter


class GovernedComputeTests(unittest.TestCase):

    def setUp(self):
        # Capture audit rows without touching the database.
        self.audit = MagicMock()
        p = patch("src.swarm.integrations.audit_capability", self.audit)
        p.start()
        self.addCleanup(p.stop)

    def _patch_resolver(self, resolver):
        p = patch("src.capabilities.resolver.get_resolver", return_value=resolver)
        p.start()
        self.addCleanup(p.stop)

    def _statuses(self):
        return [c.kwargs.get("status") for c in self.audit.call_args_list]

    # -- allow-list enforcement ------------------------------------------

    def test_destructive_command_rejected(self):
        self._patch_resolver(_FakeResolver(_compute_adapter()))
        with self.assertRaises(CapabilityDenied):
            governed_call(
                "container_compute", "run_command", ["rm", "-rf", "/"],
                actor={"uid": 7, "username": "alice"}, source="test",
            )
        self.assertIn("blocked", self._statuses())

    def test_command_not_in_allowlist_rejected(self):
        self._patch_resolver(_FakeResolver(_compute_adapter()))
        with self.assertRaises(CapabilityDenied):
            governed_call(
                "container_compute", "run_command", ["python", "evil.py"],
                actor={"uid": 1, "username": "bob"},
            )
        self.assertIn("blocked", self._statuses())

    def test_metachar_command_rejected(self):
        self._patch_resolver(_FakeResolver(_compute_adapter()))
        with self.assertRaises(CapabilityDenied):
            governed_call(
                "container_compute", "run_command", ["echo", "hi; rm x"],
            )
        self.assertIn("blocked", self._statuses())

    # -- jurisdiction rejection ------------------------------------------

    def test_out_of_jurisdiction_rejected(self):
        self._patch_resolver(
            _FakeResolver(raises=CapabilityNotAvailable("blocked by jurisdiction"))
        )
        with self.assertRaises(CapabilityNotAvailable):
            governed_call(
                "container_compute", "run_command", ["echo", "hi"],
                actor={"uid": 3, "username": "carol"},
            )
        # The rejection is audited as blocked before propagating.
        self.assertIn("blocked", self._statuses())

    # -- success path: identity + exec_locus -----------------------------

    def test_allowed_command_runs_and_audits_identity(self):
        self._patch_resolver(_FakeResolver(_compute_adapter()))
        result = governed_call(
            "container_compute", "run_command", ["echo", "governed_ok"],
            actor={"uid": 42, "username": "dave"}, source="test",
        )
        self.assertTrue(result["ok"])
        self.assertIn("governed_ok", result["stdout"])
        # Exactly one audit row, status ok, with exec_locus + identity captured.
        ok_calls = [c for c in self.audit.call_args_list if c.kwargs.get("status") == "ok"]
        self.assertEqual(len(ok_calls), 1)
        kw = ok_calls[0].kwargs
        self.assertEqual(kw.get("exec_locus"), "local")
        self.assertEqual(kw.get("actor_user_id"), 42)
        self.assertEqual(kw.get("actor_username"), "dave")

    def test_method_failure_audited_and_reraised(self):
        adapter = _compute_adapter()
        adapter.run_command = MagicMock(side_effect=RuntimeError("boom"))
        self._patch_resolver(_FakeResolver(adapter))
        with self.assertRaises(RuntimeError):
            governed_call("container_compute", "run_command", ["echo", "hi"])
        self.assertIn("error", self._statuses())


class _FakeAsyncAdapter:
    """Realtime-style adapter with an awaitable place_call (async def)."""
    exec_locus = "cloud"
    key = "twilio"

    def __init__(self):
        self.called_with = None

    async def place_call(self, *args, **kwargs):
        self.called_with = (args, kwargs)
        return {"sid": "CA_async", "status": "queued"}


class _FakeSyncAdapter:
    """Realtime-style adapter with a blocking (sync) place_call."""
    exec_locus = "cloud"
    key = "twilio"

    def __init__(self):
        self.called_with = None

    def place_call(self, *args, **kwargs):
        self.called_with = (args, kwargs)
        return {"sid": "CA_sync", "status": "queued"}


class GovernedAsyncTests(unittest.TestCase):
    """governed_call_async awaits/offloads and audits the real outcome; the sync
    governed_call refuses async methods loudly rather than auditing a lie."""

    def setUp(self):
        self.audit = MagicMock()
        p = patch("src.swarm.integrations.audit_capability", self.audit)
        p.start()
        self.addCleanup(p.stop)

    def _patch_resolver(self, resolver):
        p = patch("src.capabilities.resolver.get_resolver", return_value=resolver)
        p.start()
        self.addCleanup(p.stop)

    def _statuses(self):
        return [c.kwargs.get("status") for c in self.audit.call_args_list]

    def test_sync_call_rejects_async_method_without_running_it(self):
        adapter = _FakeAsyncAdapter()
        self._patch_resolver(_FakeResolver(adapter))
        with self.assertRaises(CapabilityDenied):
            governed_call("realtime", "place_call", "+15551112222")
        # Never invoked, and no false "ok" audit.
        self.assertIsNone(adapter.called_with)
        self.assertNotIn("ok", self._statuses())
        self.assertIn("error", self._statuses())

    def test_async_awaits_coroutine_method_and_audits_ok(self):
        adapter = _FakeAsyncAdapter()
        self._patch_resolver(_FakeResolver(adapter))
        res = asyncio.run(governed_call_async(
            "realtime", "place_call", "+15551112222",
            actor={"uid": 9, "username": "erin"}, source="telephony",
        ))
        self.assertEqual(res["sid"], "CA_async")
        self.assertIsNotNone(adapter.called_with)
        ok_calls = [c for c in self.audit.call_args_list if c.kwargs.get("status") == "ok"]
        self.assertEqual(len(ok_calls), 1)
        kw = ok_calls[0].kwargs
        self.assertEqual(kw.get("exec_locus"), "cloud")
        self.assertEqual(kw.get("actor_user_id"), 9)
        self.assertEqual(kw.get("actor_username"), "erin")

    def test_async_offloads_sync_method_and_audits_ok(self):
        adapter = _FakeSyncAdapter()
        self._patch_resolver(_FakeResolver(adapter))
        res = asyncio.run(governed_call_async("realtime", "place_call", "+15551112222"))
        self.assertEqual(res["sid"], "CA_sync")
        self.assertIsNotNone(adapter.called_with)
        self.assertIn("ok", self._statuses())

    def test_async_out_of_jurisdiction_rejected_and_audited(self):
        self._patch_resolver(
            _FakeResolver(raises=CapabilityNotAvailable("blocked by jurisdiction"))
        )
        with self.assertRaises(CapabilityNotAvailable):
            asyncio.run(governed_call_async("realtime", "place_call", "+15551112222"))
        self.assertIn("blocked", self._statuses())

    def test_async_compute_allowlist_enforced(self):
        self._patch_resolver(_FakeResolver(_compute_adapter()))
        with self.assertRaises(CapabilityDenied):
            asyncio.run(governed_call_async(
                "container_compute", "run_command_async", ["rm", "-rf", "/"],
            ))
        self.assertIn("blocked", self._statuses())


class EvaluateComputeCommandTests(unittest.TestCase):
    """PolicyEngine.evaluate_compute_command shares the SSH allow-list."""

    def _engine(self):
        from src.swarm.policy import PolicyEngine
        return PolicyEngine()

    def test_allowed_list(self):
        self.assertTrue(self._engine().evaluate_compute_command(["echo", "hi"]).allowed)

    def test_allowed_string(self):
        self.assertTrue(self._engine().evaluate_compute_command("whoami").allowed)

    def test_destructive_blocked(self):
        self.assertFalse(self._engine().evaluate_compute_command(["rm", "-rf", "/"]).allowed)

    def test_not_in_allowlist_blocked(self):
        self.assertFalse(self._engine().evaluate_compute_command(["python", "x.py"]).allowed)

    def test_empty_blocked(self):
        self.assertFalse(self._engine().evaluate_compute_command([]).allowed)


if __name__ == "__main__":
    unittest.main()
