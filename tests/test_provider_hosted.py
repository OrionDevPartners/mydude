"""Tests for the generic `provider_hosted` exec_locus (T002).

DeepSeek/Mistral/Qwen reach their own vendor APIs directly — neither Azure, nor
Anthropic-hosted, nor local. They share ONE generic exec_locus so the
jurisdiction gate and the model-promotion gate treat them correctly:

  * cloud_shift OFF  -> dropped (they are cloud egress, not local).
  * EXEC_LOCUS_PIN=local -> dropped (only local survives).
  * pin to their own locus -> they survive; in_azure pin drops them.
  * promotion gate -> their providers are the allowed set for the locus, and a
    mismatched provider (e.g. openai) is rejected for provider_hosted.

Runnable two ways:
  * ``python tests/test_provider_hosted.py``
  * ``pytest tests/test_provider_hosted.py``
"""
import os
import sys
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.providers.config import llm_enabled_keys
from src.swarm.jurisdiction import (
    provider_passes_jurisdiction,
    permitted_provider_keys,
    get_exec_locus,
)

PROVIDER_HOSTED = {"deepseek", "mistral", "qwen"}
LOCAL_PROVIDERS = {"ollama", "mlx"}


@contextmanager
def _env(**overrides):
    saved = {k: os.environ.get(k) for k in overrides}
    try:
        for k, v in overrides.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
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


# -- config wiring ------------------------------------------------------------

def test_new_providers_declared_provider_hosted():
    for key in PROVIDER_HOSTED:
        assert get_exec_locus(key) == "provider_hosted", key


# -- the single jurisdiction predicate ----------------------------------------

def test_predicate_cloud_on_no_pin_allows_provider_hosted():
    assert provider_passes_jurisdiction("provider_hosted", None, True) is True


def test_predicate_cloud_off_drops_provider_hosted():
    # provider_hosted is cloud egress, not local -> killed by the kill switch.
    assert provider_passes_jurisdiction("provider_hosted", None, False) is False


def test_predicate_local_pin_drops_provider_hosted():
    assert provider_passes_jurisdiction("provider_hosted", "local", True) is False


def test_predicate_matching_pin_allows_provider_hosted():
    assert provider_passes_jurisdiction("provider_hosted", "provider_hosted", True) is True


def test_predicate_in_azure_pin_drops_provider_hosted():
    assert provider_passes_jurisdiction("provider_hosted", "in_azure", True) is False


# -- end-to-end through permitted_provider_keys -------------------------------

def test_provider_hosted_routable_when_cloud_on():
    with _env(CLOUD_SHIFT_ENABLED="true", EXEC_LOCUS_PIN=None):
        keys = set(permitted_provider_keys())
    enabled = set(llm_enabled_keys())
    assert (PROVIDER_HOSTED & enabled) <= keys, keys


def test_provider_hosted_dropped_when_cloud_off():
    with _env(CLOUD_SHIFT_ENABLED="false", EXEC_LOCUS_PIN=None):
        keys = set(permitted_provider_keys())
    assert not (PROVIDER_HOSTED & keys), keys
    # Only local providers remain, exactly as before this locus existed.
    assert keys == (LOCAL_PROVIDERS & set(llm_enabled_keys())), keys


def test_provider_hosted_dropped_when_pinned_local():
    with _env(CLOUD_SHIFT_ENABLED="true", EXEC_LOCUS_PIN="local"):
        keys = set(permitted_provider_keys())
    assert not (PROVIDER_HOSTED & keys), keys


def test_provider_hosted_only_when_pinned_to_locus():
    with _env(CLOUD_SHIFT_ENABLED="true", EXEC_LOCUS_PIN="provider_hosted"):
        keys = set(permitted_provider_keys())
    enabled = set(llm_enabled_keys())
    assert keys == (PROVIDER_HOSTED & enabled), keys


# -- infra model-promotion gate -----------------------------------------------

def _gate():
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "infra", "mydude", "gates"))
    import model_promotion_gate as G
    return G


def test_gate_exec_locus_providers_includes_provider_hosted():
    G = _gate()
    assert G.EXEC_LOCUS_PROVIDERS.get("provider_hosted") == PROVIDER_HOSTED


def test_gate_asserts_matching_provider_passes():
    G = _gate()
    # Should not raise: deepseek is a valid provider for provider_hosted.
    G._assert_exec_locus("deepseek-v4", "deepseek", "provider_hosted")


def test_gate_rejects_mismatched_provider():
    G = _gate()
    raised = False
    try:
        G._assert_exec_locus("gpt-5.5", "openai", "provider_hosted")
    except G.ExecLocusViolation:
        raised = True
    assert raised, "openai must not satisfy provider_hosted"


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
