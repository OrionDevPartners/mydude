"""Tests for the shared governed-swarm service + MCP tool surface (T004).

The whole point of T004 is that REST and MCP run the EXACT same governed pipeline
— so these tests pin the shared seam (:mod:`src.swarm.service`) and the MCP tool
that wraps it, all offline (no providers, no network, no DB):

  * input bounds (empty / over-long prompt) fail loud with safe messages,
  * the fail-loud provider guard fires (and can be deliberately skipped by the
    web callers that pre-check),
  * normalize_scores projects the verbose governance envelope into the compact
    shape every surface shares,
  * the MCP tool returns the synthesis + compact scores + the FULL governance
    envelope (no raw passthrough) and maps service errors to actionable messages.

Runnable as ``python tests/test_governed_service.py`` or under pytest.
"""
import asyncio
import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("LLM_PROVIDER", "stub")
warnings.filterwarnings("ignore")

from src.swarm import service
from src.swarm.service import (
    MAX_PROMPT_LEN, SwarmInputError, SwarmUnavailable,
    normalize_scores, run_governed_swarm,
)


# A representative governed result envelope (what orchestrator.run returns).
def _governed_result():
    return {
        "SYNTHESIS": "the governed answer",
        "COMPLIANCE_SCORES": [{"score": 90}, {"score": 80}],
        "HALLUCINATION_RISK": {"average": 0.12, "tier": "LOW"},
        "JURISDICTION": {"domain": "engineering", "team": "default"},
        "BENCHMARK_ROUTING": {
            "category": "frontend_uiux",
            "lead_provider": "gemini",
            "lead_specialty": "visual/UX",
            "classification_signal": "matched: ui, ux",
            "bias_applied": True,
            "bias_delta": 0.1,
        },
        "DISSENT_LOG": ["x dissented"],
        "CLAIM_LEDGER": "1 claim",
    }


# ───────────────────────── normalize_scores ─────────────────────────

def test_normalize_scores_full_envelope():
    s = normalize_scores(_governed_result())
    assert s["compliance"] == 0.85, s            # (90+80)/2/100
    assert s["hallucination_risk"] == 0.12, s
    assert s["jurisdiction"] == "engineering \u00b7 default", s
    b = s["benchmark"]
    assert b["category"] == "frontend_uiux", b
    assert b["lead_provider"] == "gemini", b
    assert b["lead_specialty"] == "visual/UX", b
    assert b["classification_signal"] == "matched: ui, ux", b
    assert b["bias_applied"] is True, b


def test_normalize_scores_empty_and_partial():
    assert normalize_scores({}) == {}
    # scalar hallucination + string jurisdiction + no benchmark category
    s = normalize_scores({
        "HALLUCINATION_RISK": 0.4,
        "JURISDICTION": "  legal  ",
        "BENCHMARK_ROUTING": {"category": ""},
    })
    assert s == {"hallucination_risk": 0.4, "jurisdiction": "legal"}, s


def test_normalize_scores_ignores_nonnumeric_compliance():
    s = normalize_scores({"COMPLIANCE_SCORES": [{"score": "n/a"}, {"nope": 1}]})
    assert "compliance" not in s, s


# ───────────────────────── run_governed_swarm guards ─────────────────────────

def test_run_rejects_empty_prompt():
    try:
        asyncio.run(run_governed_swarm("   ", check_providers=False))
        assert False, "expected SwarmInputError"
    except SwarmInputError as e:
        assert "prompt" in str(e).lower(), e


def test_run_rejects_overlong_prompt():
    try:
        asyncio.run(run_governed_swarm("x" * (MAX_PROMPT_LEN + 1), check_providers=False))
        assert False, "expected SwarmInputError"
    except SwarmInputError as e:
        assert "too long" in str(e).lower(), e


def test_run_fails_loud_when_no_provider(monkeypatch_like=None):
    orig = service.llm_providers_available
    service.llm_providers_available = lambda: False
    try:
        asyncio.run(run_governed_swarm("real prompt", check_providers=True))
        assert False, "expected SwarmUnavailable"
    except SwarmUnavailable as e:
        assert "provider" in str(e).lower(), e
    finally:
        service.llm_providers_available = orig


class _FakeOrch:
    last = {}

    def __init__(self, broker):
        pass

    async def run(self, goal, domain="general", team="default", task_run_id=None):
        _FakeOrch.last = {"goal": goal, "domain": domain, "team": team, "task_run_id": task_run_id}
        return _governed_result()


def _patch_orchestrator():
    """Swap the four constructors the service imports so no real swarm runs."""
    import src.swarm.broker as broker_mod
    import src.swarm.policy as policy_mod
    import src.swarm.integrations as integ_mod
    import src.swarm.orchestrator as orch_mod

    saved = (broker_mod.CapabilityBroker, policy_mod.PolicyEngine,
             integ_mod.Integrations, orch_mod.WaveOrchestrator)
    broker_mod.CapabilityBroker = lambda *a, **k: object()
    policy_mod.PolicyEngine = lambda *a, **k: object()
    integ_mod.Integrations = lambda *a, **k: object()
    orch_mod.WaveOrchestrator = _FakeOrch

    def restore():
        (broker_mod.CapabilityBroker, policy_mod.PolicyEngine,
         integ_mod.Integrations, orch_mod.WaveOrchestrator) = saved
    return restore


def test_run_happy_path_builds_orchestrator_and_returns_envelope():
    restore = _patch_orchestrator()
    try:
        # check_providers=False mirrors the web callers (they pre-check); prompt
        # must be stripped and the domain/team normalized before the run.
        result = asyncio.run(run_governed_swarm(
            "  design a UI  ", domain="design", team="default", task_run_id=7,
            check_providers=False,
        ))
        assert result["SYNTHESIS"] == "the governed answer", result
        assert _FakeOrch.last["goal"] == "design a UI", _FakeOrch.last  # stripped
        assert _FakeOrch.last["task_run_id"] == 7, _FakeOrch.last
        assert isinstance(_FakeOrch.last["domain"], str) and _FakeOrch.last["domain"], _FakeOrch.last
    finally:
        restore()


def test_run_skips_provider_check_when_disabled():
    # Even with NO provider available, check_providers=False must proceed (the web
    # endpoints already gated availability before taking the row/guard).
    restore = _patch_orchestrator()
    orig = service.llm_providers_available
    service.llm_providers_available = lambda: False
    try:
        result = asyncio.run(run_governed_swarm("go", check_providers=False))
        assert result["SYNTHESIS"] == "the governed answer", result
    finally:
        service.llm_providers_available = orig
        restore()


# ───────────────────────── MCP tool wiring ─────────────────────────

def _import_mcp_server():
    from src.mcp import server as mcp_server
    return mcp_server


def test_mcp_registers_single_governed_tool():
    mcp_server = _import_mcp_server()
    tools = asyncio.run(mcp_server.mcp.list_tools())
    assert len(tools) == 1, [t.name for t in tools]
    t = tools[0]
    assert t.name == "run_governed_swarm", t.name
    props = set((t.inputSchema or {}).get("properties", {}).keys())
    assert props == {"prompt", "domain", "team"}, props
    assert t.outputSchema, "tool must expose a structured output schema"
    # bounded prompt — the schema must carry the max length
    assert (t.inputSchema["properties"]["prompt"].get("maxLength") == MAX_PROMPT_LEN), t.inputSchema


def test_mcp_tool_returns_full_governance_envelope():
    mcp_server = _import_mcp_server()

    async def _fake_run(prompt, domain="general", team="default", check_providers=True):
        assert check_providers is True, "MCP must enforce the provider guard"
        return _governed_result()

    orig = mcp_server.run_governed_swarm
    mcp_server.run_governed_swarm = _fake_run
    try:
        out = asyncio.run(mcp_server.run_governed_swarm_tool(prompt="design a UI", domain="design"))
        assert out["synthesis"] == "the governed answer", out
        # compact scores match the shared projection
        assert out["scores"] == normalize_scores(_governed_result()), out["scores"]
        # FULL governance envelope present — proves no raw passthrough
        gov = out["governance"]
        for key in ("COMPLIANCE_SCORES", "HALLUCINATION_RISK", "DISSENT_LOG",
                    "CLAIM_LEDGER", "JURISDICTION", "BENCHMARK_ROUTING"):
            assert key in gov, key
    finally:
        mcp_server.run_governed_swarm = orig


def test_mcp_tool_maps_input_error_to_actionable_message():
    mcp_server = _import_mcp_server()

    async def _raise_input(*a, **k):
        raise SwarmInputError("Prompt is too long (max 8000 characters).")

    orig = mcp_server.run_governed_swarm
    mcp_server.run_governed_swarm = _raise_input
    try:
        try:
            asyncio.run(mcp_server.run_governed_swarm_tool(prompt="x"))
            assert False, "expected ValueError"
        except ValueError as e:
            assert "too long" in str(e), e
    finally:
        mcp_server.run_governed_swarm = orig


def test_mcp_tool_maps_unavailable_to_actionable_message():
    mcp_server = _import_mcp_server()

    async def _raise_unavail(*a, **k):
        raise SwarmUnavailable("No LLM provider is configured.")

    orig = mcp_server.run_governed_swarm
    mcp_server.run_governed_swarm = _raise_unavail
    try:
        try:
            asyncio.run(mcp_server.run_governed_swarm_tool(prompt="x"))
            assert False, "expected ValueError"
        except ValueError as e:
            assert "provider" in str(e).lower(), e
    finally:
        mcp_server.run_governed_swarm = orig


def test_mcp_tool_hides_internal_errors():
    mcp_server = _import_mcp_server()

    async def _boom(*a, **k):
        raise RuntimeError("secret provider stack trace detail")

    orig = mcp_server.run_governed_swarm
    mcp_server.run_governed_swarm = _boom
    try:
        try:
            asyncio.run(mcp_server.run_governed_swarm_tool(prompt="x"))
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "secret provider stack trace" not in str(e), e
            assert "server logs" in str(e).lower(), e
    finally:
        mcp_server.run_governed_swarm = orig


# ───────────────────────── llm_providers_available ─────────────────────────

class _Spec:
    def __init__(self, secrets):
        self.secrets = secrets


class _Adapter:
    def __init__(self, up):
        self._up = up

    def is_available(self):
        return self._up


def _patch_availability(specs, present_secrets, adapter_up):
    """Patch the three collaborators llm_providers_available imports at call time.

    Returns a (restore, calls) pair; ``calls['built']`` counts build_adapter calls
    so a test can prove the cheap keyed path short-circuits before any probe.
    """
    import src.providers.config as config_mod
    import src.providers.secrets as secrets_mod
    import src.providers.registry as registry_mod

    saved = (config_mod.llm_provider_specs, secrets_mod.has_secret,
             registry_mod.build_adapter)
    calls = {"built": 0}

    def _build(spec):
        calls["built"] += 1
        return _Adapter(adapter_up)

    config_mod.llm_provider_specs = lambda: specs
    secrets_mod.has_secret = lambda name: name in present_secrets
    registry_mod.build_adapter = _build

    def restore():
        (config_mod.llm_provider_specs, secrets_mod.has_secret,
         registry_mod.build_adapter) = saved
    return restore, calls


def test_available_keyed_provider_shortcircuits_without_probe():
    # A keyed provider whose secret resolves => available, and NO local probe is
    # attempted (build_adapter must not be called) — the cheap path wins.
    restore, calls = _patch_availability(
        [_Spec(["OPENAI_API_KEY"]), _Spec([])],
        present_secrets={"OPENAI_API_KEY"},
        adapter_up=False,
    )
    try:
        assert service.llm_providers_available() is True
        assert calls["built"] == 0, calls
    finally:
        restore()


def test_available_local_only_counts_when_server_up():
    # No keyed provider, but a secretless local provider whose server is listening.
    restore, calls = _patch_availability(
        [_Spec(["OPENAI_API_KEY"]), _Spec([])],
        present_secrets=set(),  # cloud key missing
        adapter_up=True,        # local server up
    )
    try:
        assert service.llm_providers_available() is True
        assert calls["built"] >= 1, calls
    finally:
        restore()


def test_available_local_only_false_when_server_down():
    # No keyed provider and the local server is down => not available (fail loud).
    restore, calls = _patch_availability(
        [_Spec(["OPENAI_API_KEY"]), _Spec([])],
        present_secrets=set(),
        adapter_up=False,
    )
    try:
        assert service.llm_providers_available() is False
    finally:
        restore()


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
