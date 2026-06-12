"""Tests for the local-model fallback safety net.

These guard the behaviour that keeps MyDude working when cloud AI is disabled
(the cloud_shift kill switch) or unavailable — without any running local
inference server, secret, or network. Three layers are covered:

  1. permitted_provider_keys() — the single jurisdiction seam the live swarm
     (MultiProviderLLM._available_adapters) routes through. It must drop every
     cloud provider when CLOUD_SHIFT_ENABLED=false or EXEC_LOCUS_PIN=local, and
     allow all enabled providers otherwise.
  2. The local adapters (Ollama/MLX) — they must report *unavailable* when no
     server is listening (so a dead local box never poisons the fanout), and
     resolve_model() must prefer an installed registry model over the static
     config default.
  3. The infra jurisdiction router — with no policy DB configured it must still
     degrade to a local provider (Ollama/MLX) at the local_degraded tier rather
     than refusing.

Runnable two ways:
  * ``python tests/test_local_fallback.py``  (standalone, exits non-zero on failure)
  * ``pytest tests/test_local_fallback.py``   (test_* functions; no plugins needed)
"""
import asyncio
import os
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.providers.config import llm_enabled_keys
from src.providers.registry import build_adapter
from src.providers.config import defined_provider_specs
from src.swarm.jurisdiction import permitted_provider_keys, get_cloud_shift

# exec_locus values declared in config/providers.toml (env_1).
LOCAL_PROVIDERS = {"ollama", "mlx"}
CLOUD_PROVIDERS = {"openai", "anthropic", "gemini", "grok"}

OLLAMA_DEFAULT_MODEL = "llama3.2:3b"


@contextmanager
def _env(**overrides):
    """Temporarily set/unset env vars, restoring the prior state afterwards."""
    saved = {k: os.environ.get(k) for k in overrides}
    try:
        for k, v in overrides.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        # The cloud_shift lookup is cached for a few seconds; force a fresh read
        # so an env change is observed immediately within the test.
        import src.swarm.jurisdiction as J
        J._cloud_shift_cache = None
        J._cloud_shift_ts = 0.0
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import src.swarm.jurisdiction as J
        J._cloud_shift_cache = None
        J._cloud_shift_ts = 0.0


# -- permitted_provider_keys() ------------------------------------------------

def test_all_providers_permitted_by_default():
    # cloud_shift on, no exec_locus pin -> every enabled provider is permitted.
    with _env(CLOUD_SHIFT_ENABLED="true", EXEC_LOCUS_PIN=None):
        keys = set(permitted_provider_keys())
    enabled = set(llm_enabled_keys())
    assert keys == enabled, (keys, enabled)
    # Sanity: both cloud and local providers are present in the default set.
    assert CLOUD_PROVIDERS & keys, keys
    assert LOCAL_PROVIDERS & keys, keys


def test_cloud_shift_off_leaves_only_local():
    for off_value in ("false", "0", "no", "off"):
        with _env(CLOUD_SHIFT_ENABLED=off_value, EXEC_LOCUS_PIN=None):
            keys = set(permitted_provider_keys())
        assert keys == (LOCAL_PROVIDERS & set(llm_enabled_keys())), (off_value, keys)
        assert not (CLOUD_PROVIDERS & keys), (off_value, keys)


def test_exec_locus_pin_local_leaves_only_local():
    # Even with cloud_shift ON, pinning the locus to 'local' drops cloud providers.
    with _env(CLOUD_SHIFT_ENABLED="true", EXEC_LOCUS_PIN="local"):
        keys = set(permitted_provider_keys())
    assert keys == (LOCAL_PROVIDERS & set(llm_enabled_keys())), keys
    assert not (CLOUD_PROVIDERS & keys), keys


def test_exec_locus_pin_cloud_drops_local():
    # Pinning to a cloud locus keeps only providers in that locus.
    with _env(CLOUD_SHIFT_ENABLED="true", EXEC_LOCUS_PIN="in_azure"):
        keys = set(permitted_provider_keys())
    # in_azure providers from env_1 are openai, gemini, grok (anthropic is hosted).
    assert "openai" in keys and "gemini" in keys and "grok" in keys, keys
    assert "anthropic" not in keys, keys
    assert not (LOCAL_PROVIDERS & keys), keys


def test_explicit_args_override_env():
    # Explicit kwargs must win over the ambient env so callers (the live swarm)
    # can pass an applied jurisdiction decision directly.
    with _env(CLOUD_SHIFT_ENABLED="false", EXEC_LOCUS_PIN="local"):
        keys = set(
            permitted_provider_keys(
                provider_keys=["openai", "ollama"],
                exec_locus_pin="any",
                cloud_shift_active=True,
            )
        )
    assert keys == {"openai", "ollama"}, keys


def test_pin_any_is_treated_as_no_pin():
    with _env(CLOUD_SHIFT_ENABLED="true", EXEC_LOCUS_PIN="any"):
        keys = set(permitted_provider_keys())
    assert keys == set(llm_enabled_keys()), keys


# -- local adapters: availability + model resolution --------------------------

def _local_spec(key: str):
    return defined_provider_specs()[key]


def test_local_adapter_unavailable_when_no_server_listening():
    # Point the adapter at a closed port; the TCP probe must report it down so
    # the swarm never adds a dead local box to the fanout.
    spec = _local_spec("ollama")
    with _env(OLLAMA_BASE_URL="http://127.0.0.1:1/v1", OLLAMA_PROBE_TIMEOUT="0.2"):
        adapter = build_adapter(spec)
        assert adapter.is_available() is False, "dead local server must be unavailable"


def test_local_adapter_resolves_registry_model_over_config_default():
    # A registry that lists a different model for ollama must win over the
    # config default_model (llama3.2:3b).
    manifest = (
        "models:\n"
        "  - model_id: qwen2.5:7b\n"
        "    provider: ollama\n"
    )
    spec = _local_spec("ollama")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "model_registry.yaml")
        with open(path, "w") as f:
            f.write(manifest)
        # Ensure no OLLAMA_MODEL env pin (that would take precedence over both).
        with _env(LOCAL_MODEL_REGISTRY_PATH=path, OLLAMA_MODEL=None):
            adapter = build_adapter(spec)
            model = asyncio.run(adapter.resolve_model())
    assert model == "qwen2.5:7b", model
    assert model != OLLAMA_DEFAULT_MODEL, model


def test_local_adapter_falls_back_to_config_default_without_registry():
    spec = _local_spec("ollama")
    with tempfile.TemporaryDirectory() as d:
        missing = os.path.join(d, "nope", "model_registry.yaml")
        with _env(LOCAL_MODEL_REGISTRY_PATH=missing, OLLAMA_MODEL=None):
            adapter = build_adapter(spec)
            model = asyncio.run(adapter.resolve_model())
    assert model == OLLAMA_DEFAULT_MODEL, model


# -- infra jurisdiction router: local_degraded without a policy DB -------------

def _router():
    # Import lazily; the infra package is reachable from the repo root.
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    "infra", "mydude", "routing"))
    from jurisdiction import JurisdictionRouter, FallbackTier, ExecLocus, Outcome
    return JurisdictionRouter, FallbackTier, ExecLocus, Outcome


def test_router_degrades_to_local_when_no_policy_db():
    JurisdictionRouter, FallbackTier, ExecLocus, Outcome = _router()
    # No PG_AGENTS_HOME_DSN -> no policy rows; cloud_shift defaults to enabled.
    with _env(PG_AGENTS_HOME_DSN=None):
        decision = JurisdictionRouter(dsn="").decide(domain="general", team="default")
    assert decision.fallback_tier == FallbackTier.LOCAL_DEGRADED, decision.fallback_tier
    assert decision.exec_locus == ExecLocus.LOCAL, decision.exec_locus
    assert decision.resolved_provider in LOCAL_PROVIDERS, decision.resolved_provider
    assert decision.resolved_model, decision.resolved_model
    assert decision.outcome == Outcome.EXECUTED, decision.outcome


def test_router_local_only_request_resolves_local_provider():
    JurisdictionRouter, FallbackTier, ExecLocus, Outcome = _router()
    with _env(PG_AGENTS_HOME_DSN=None):
        decision = JurisdictionRouter(dsn="").decide(
            domain="general", team="default", local_only=True
        )
    assert decision.exec_locus == ExecLocus.LOCAL, decision.exec_locus
    assert decision.fallback_tier == FallbackTier.LOCAL_DEGRADED, decision.fallback_tier
    assert decision.resolved_provider in LOCAL_PROVIDERS, decision.resolved_provider


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
