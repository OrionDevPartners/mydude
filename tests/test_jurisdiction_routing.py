"""Regression tests for jurisdiction routing enforcement.

Jurisdiction routing is a governance-critical guarantee: a run must never egress
to a cloud provider that the ``cloud_shift`` kill switch (or an ``exec_locus``
pin) was meant to block. The enforcement lives at the provider-selection layer in
``MultiProviderLLM`` and is recorded onto each task run, so a silent regression
here could let governed work leak to the wrong jurisdiction without anyone
noticing. These tests pin down that behaviour end to end:

  * ``_available_adapters()`` drops every non-local provider when the
    ``cloud_shift`` kill switch is off, and keeps only the providers whose
    ``exec_locus`` matches an active pin.
  * ``effective_routing()`` reports the correct ``(fallback_tier, exec_locus,
    outcome)`` for the preferred (1), local_degraded (4) and refuse (5) rungs of
    the fallback ladder.
  * ``WaveOrchestrator.run()`` emits the ``JURISDICTION`` block, and the
    ``/tasks/run`` route (``routes_tasks``) persists it into
    ``TaskRun.provider_scores``.

The exec_locus expectations below mirror ``config/providers.toml`` (committed):
openai/gemini/grok = ``in_azure``, anthropic = ``anthropic_hosted``,
ollama/mlx = ``local``.

Runnable two ways:
  * ``python tests/test_jurisdiction_routing.py``  (standalone, exits non-zero on failure)
  * ``pytest tests/test_jurisdiction_routing.py``   (test_* functions; no plugins needed)
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force the swarm into stub mode BEFORE importing the orchestrator so the run
# never opens a provider client or hits the network. The orchestrator reads
# LLM_PROVIDER at import time; we also override the module global after import
# for determinism regardless of the ambient environment.
os.environ["LLM_PROVIDER"] = "stub"

from src.swarm.llm_multi import MultiProviderLLM


class FakeAdapter:
    """Minimal stand-in for an LLM adapter.

    ``_available_adapters`` only needs ``.key`` (to look up the real exec_locus
    from config) and ``.is_available()``; everything else on the swarm is left
    untouched. Using real provider keys means we exercise the real
    ``get_exec_locus`` mapping from ``config/providers.toml``.
    """

    def __init__(self, key, available=True):
        self.key = key
        self._available = available

    def is_available(self):
        return self._available


def _llm_with(adapters):
    llm = MultiProviderLLM()
    llm.adapters = adapters
    return llm


# ---------------------------------------------------------------------------
# 1. cloud_shift kill switch drops every non-local provider
# ---------------------------------------------------------------------------
def test_cloud_shift_off_drops_all_non_local():
    llm = _llm_with([
        FakeAdapter("openai"),     # in_azure
        FakeAdapter("anthropic"),  # anthropic_hosted
        FakeAdapter("gemini"),     # in_azure
        FakeAdapter("ollama"),     # local
        FakeAdapter("mlx"),        # local
    ])

    # Kill switch off: only the two local providers survive.
    llm.apply_jurisdiction(exec_locus_pin="any", cloud_shift_active=False)
    survivors = {a.key for a in llm._available_adapters()}
    assert survivors == {"ollama", "mlx"}, survivors

    # Kill switch on: all available providers pass the filter.
    llm.apply_jurisdiction(exec_locus_pin="any", cloud_shift_active=True)
    survivors = {a.key for a in llm._available_adapters()}
    assert survivors == {"openai", "anthropic", "gemini", "ollama", "mlx"}, survivors


def test_cloud_shift_off_with_no_local_refuses_everything():
    # Honest behaviour: kill switch on and no local provider declared -> nobody
    # is routable (the ladder resolves to refuse, asserted separately below).
    llm = _llm_with([FakeAdapter("openai"), FakeAdapter("anthropic")])
    llm.apply_jurisdiction(exec_locus_pin="any", cloud_shift_active=False)
    assert llm._available_adapters() == []


def test_unavailable_adapters_are_always_dropped():
    # Jurisdiction filtering composes with availability: a provider whose secret
    # is missing never enters the fanout even if its locus would pass.
    llm = _llm_with([
        FakeAdapter("openai", available=False),
        FakeAdapter("ollama", available=True),
    ])
    llm.apply_jurisdiction(exec_locus_pin="any", cloud_shift_active=True)
    survivors = {a.key for a in llm._available_adapters()}
    assert survivors == {"ollama"}, survivors


# ---------------------------------------------------------------------------
# 2. exec_locus pin keeps only matching providers
# ---------------------------------------------------------------------------
def test_exec_locus_pin_keeps_only_matching():
    adapters = [
        FakeAdapter("openai"),     # in_azure
        FakeAdapter("anthropic"),  # anthropic_hosted
        FakeAdapter("gemini"),     # in_azure
        FakeAdapter("ollama"),     # local
    ]

    # pin=anthropic_hosted -> only anthropic.
    llm = _llm_with(list(adapters))
    llm.apply_jurisdiction(exec_locus_pin="anthropic_hosted", cloud_shift_active=True)
    assert {a.key for a in llm._available_adapters()} == {"anthropic"}

    # pin=in_azure -> only the Azure-hosted providers.
    llm = _llm_with(list(adapters))
    llm.apply_jurisdiction(exec_locus_pin="in_azure", cloud_shift_active=True)
    assert {a.key for a in llm._available_adapters()} == {"openai", "gemini"}

    # pin=local -> only local providers.
    llm = _llm_with(list(adapters))
    llm.apply_jurisdiction(exec_locus_pin="local", cloud_shift_active=True)
    assert {a.key for a in llm._available_adapters()} == {"ollama"}


def test_pin_with_no_match_yields_nothing():
    llm = _llm_with([FakeAdapter("openai"), FakeAdapter("gemini")])
    llm.apply_jurisdiction(exec_locus_pin="anthropic_hosted", cloud_shift_active=True)
    assert llm._available_adapters() == []


# ---------------------------------------------------------------------------
# 3. effective_routing() reports the right fallback-ladder rung
# ---------------------------------------------------------------------------
def test_effective_routing_preferred_tier_1():
    # A routable cloud provider, kill switch on, no pin -> preferred (tier 1).
    llm = _llm_with([FakeAdapter("openai")])
    llm.apply_jurisdiction(exec_locus_pin="any", cloud_shift_active=True)
    assert llm.effective_routing() == (1, "in_azure", "executed")


def test_effective_routing_preferred_tier_1_with_pin_reports_pin():
    llm = _llm_with([FakeAdapter("anthropic")])
    llm.apply_jurisdiction(exec_locus_pin="anthropic_hosted", cloud_shift_active=True)
    assert llm.effective_routing() == (1, "anthropic_hosted", "executed")


def test_effective_routing_local_degraded_tier_4():
    # Only a local provider survives -> local_degraded (tier 4), even with the
    # kill switch still on (cloud simply isn't available).
    llm = _llm_with([FakeAdapter("ollama")])
    llm.apply_jurisdiction(exec_locus_pin="any", cloud_shift_active=True)
    assert llm.effective_routing() == (4, "local", "degraded")


def test_effective_routing_kill_switch_forces_local_degraded_tier_4():
    # Kill switch off with a local provider present -> degraded to local.
    llm = _llm_with([FakeAdapter("openai"), FakeAdapter("ollama")])
    llm.apply_jurisdiction(exec_locus_pin="any", cloud_shift_active=False)
    assert llm.effective_routing() == (4, "local", "degraded")


def test_effective_routing_refuse_tier_5_kill_switch_no_local():
    # Kill switch off and only cloud providers exist -> refuse (tier 5).
    llm = _llm_with([FakeAdapter("openai"), FakeAdapter("anthropic")])
    llm.apply_jurisdiction(exec_locus_pin="any", cloud_shift_active=False)
    assert llm.effective_routing() == (5, "local", "refused")


def test_effective_routing_refuse_tier_5_pin_no_match():
    # Kill switch on but the pin matches nothing routable -> refuse, reporting
    # the pinned locus that could not be satisfied.
    llm = _llm_with([FakeAdapter("openai"), FakeAdapter("gemini")])
    llm.apply_jurisdiction(exec_locus_pin="anthropic_hosted", cloud_shift_active=True)
    assert llm.effective_routing() == (5, "anthropic_hosted", "refused")


# ---------------------------------------------------------------------------
# 4. Orchestrator emits the JURISDICTION block; the route persists it
# ---------------------------------------------------------------------------
_JURISDICTION_KEYS = {
    "domain", "team", "exec_locus", "fallback_tier",
    "cloud_shift_active", "outcome", "source",
}


class _FakePolicyDecision:
    """Stand-in for PolicyDecision; the orchestrator only reads ``.reason``."""
    reason = "stub: capability not executed under test"


class _FakeBrokerResult:
    ok = True
    output = None
    decision = _FakePolicyDecision()


class _FakeBroker:
    """Broker that approves nothing and executes nothing.

    Replaces CapabilityBroker so a stub-mode orchestrator run never reaches the
    real Integrations layer (git/terraform/subprocess). That keeps the run
    hermetic and side-effect-free — the governed wave loop still executes and
    still emits the JURISDICTION block, which is what we are asserting.
    """

    async def request(self, capability, params):
        return _FakeBrokerResult()


def _run_stub_orchestrator():
    """Run a real WaveOrchestrator in stub mode (no providers, no capabilities).

    Forces stub mode so no provider client/network is touched, and injects a
    no-op broker so no capability (e.g. git_status) is ever executed.
    """
    import src.swarm.orchestrator as orch_mod
    orch_mod.LLM_PROVIDER = "stub"
    orch = orch_mod.WaveOrchestrator(_FakeBroker())
    return asyncio.run(orch.run("verify jurisdiction routing emits its block"))


def test_orchestrator_emits_jurisdiction_block():
    result = _run_stub_orchestrator()
    assert "JURISDICTION" in result, list(result.keys())
    block = result["JURISDICTION"]
    assert isinstance(block, dict), block
    assert _JURISDICTION_KEYS <= set(block.keys()), set(block.keys())


# A deterministic JURISDICTION payload shaped exactly like the block emitted by
# WaveOrchestrator.run() (see test_orchestrator_emits_jurisdiction_block). Used
# by the persistence test so it does not have to re-run the full wave loop.
_FIXTURE_RESULT = {
    "COMPLIANCE_SCORES": {"openai": 95},
    "HALLUCINATION_RISK": {"average": 0.1, "trend": "stable", "tier": "low"},
    "JURISDICTION": {
        "domain": "general",
        "team": "default",
        "exec_locus": "in_azure",
        "fallback_tier": 1,
        "cloud_shift_active": True,
        "outcome": "executed",
        "source": "env_fallback",
    },
}


def test_routes_tasks_persists_jurisdiction_into_provider_scores():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from src.models import TaskRun
    from src.web import routes_tasks
    import src.swarm.orchestrator as orch_mod

    # The block we expect to see round-trip into the DB.
    emitted = _FIXTURE_RESULT
    assert _JURISDICTION_KEYS <= set(emitted["JURISDICTION"].keys())

    # Isolated in-memory DB with just the task_runs table.
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    TaskRun.__table__.create(engine)
    TestSession = sessionmaker(bind=engine)

    class _FakeOrchestrator:
        """Stands in for WaveOrchestrator: returns the already-emitted result so
        the route's persistence path is what's under test (not the run again)."""

        def __init__(self, broker):
            self.broker = broker

        async def run(self, prompt, **kwargs):
            return emitted

    class _FakeClient:
        host = "127.0.0.1"

    class _FakeRequest:
        def __init__(self):
            self.headers = {}
            self.client = _FakeClient()

    # Patch the route's collaborators: in-memory DB, the orchestrator class the
    # route imports at call time, and the pre-flight gates that need real data.
    saved = {
        "WaveOrchestrator": orch_mod.WaveOrchestrator,
        "SessionLocal": routes_tasks.SessionLocal,
        "_has_active_keys": routes_tasks._has_active_keys,
        "_llm_providers_available": routes_tasks._llm_providers_available,
    }
    orch_mod.WaveOrchestrator = _FakeOrchestrator
    routes_tasks.SessionLocal = TestSession
    routes_tasks._has_active_keys = lambda: True
    routes_tasks._llm_providers_available = lambda: True
    routes_tasks._run_limiter._events.clear()
    routes_tasks._run_guard._active = 0

    try:
        asyncio.run(routes_tasks.run_task(_FakeRequest(), prompt="persist me", _=None))

        session = TestSession()
        try:
            row = session.query(TaskRun).order_by(TaskRun.id.desc()).first()
        finally:
            session.close()
    finally:
        orch_mod.WaveOrchestrator = saved["WaveOrchestrator"]
        routes_tasks.SessionLocal = saved["SessionLocal"]
        routes_tasks._has_active_keys = saved["_has_active_keys"]
        routes_tasks._llm_providers_available = saved["_llm_providers_available"]
        routes_tasks._run_limiter._events.clear()
        routes_tasks._run_guard._active = 0

    assert row is not None, "task run row was not created"
    assert row.status == "completed", row.status
    assert row.provider_scores, "provider_scores was not persisted"
    scores = json.loads(row.provider_scores)
    assert "jurisdiction" in scores, scores
    # routes_tasks now persists the UNIFIED compact governance summary (the same
    # shape the SPA endpoint and the MCP server use, via service.normalize_scores),
    # so jurisdiction round-trips as the compact "domain \u00b7 team" string rather
    # than the raw orchestrator dict.
    from src.swarm.service import normalize_scores
    assert scores["jurisdiction"] == normalize_scores(emitted)["jurisdiction"], scores["jurisdiction"]
    assert scores["jurisdiction"] == "general \u00b7 default", scores["jurisdiction"]


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
