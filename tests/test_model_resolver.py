"""Tests for AUTOROTATE — vendor-agnostic model version parsing + selection.

These guard the behaviour that lets each provider family auto-resolve UP to the
newest model the live API lists, while operator pins (anchored patterns / alias
env / *_MODEL env) still win. No vendor model id is hardcoded in the resolver;
all selection is driven by config/providers.toml patterns, so these tests also
assert the committed config rotates the families we intend and keeps Gemini
pinned.

Runnable two ways:
  * ``python tests/test_model_resolver.py``  (standalone, exits non-zero on failure)
  * ``pytest tests/test_model_resolver.py``   (test_* functions; no plugins needed)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.swarm.model_resolver import (
    _version_tuple,
    _tier_rank,
    _semver_key,
    pick_latest_matching,
)
from src.providers.config import defined_provider_specs, llm_enabled_keys


# -- version parsing ----------------------------------------------------------

def test_version_tuple_dotted_semver():
    assert _version_tuple("gpt-5.5") == (5, 5, 0)
    assert _version_tuple("claude-3.7") == (3, 7, 0)
    assert _version_tuple("qwen2.5-max") == (2, 5, 0)
    assert _version_tuple("grok-4.3") == (4, 3, 0)


def test_version_tuple_vN_style():
    assert _version_tuple("deepseek-v4") == (4, 0, 0)
    assert _version_tuple("deepseek-v3-1") == (3, 1, 0)


def test_version_tuple_dashed_family():
    assert _version_tuple("claude-opus-4-8") == (4, 8, 0)
    assert _version_tuple("claude-fable-5") == (5, 0, 0)


def test_version_tuple_attached_digit():
    assert _version_tuple("grok4") == (4, 0, 0)
    assert _version_tuple("qwen3-max") == (3, 0, 0)


def test_version_tuple_strips_date_snapshot():
    # An 8-digit release date must never be read as a version segment.
    assert _version_tuple("claude-opus-4-20250514") == (4, 0, 0)
    assert _version_tuple("gpt-5.5-2026-01-01"[:9]) == (5, 5, 0)


def test_version_tuple_unknown_is_zero():
    assert _version_tuple("some-text-only") == (0, 0, 0)


# -- tier ranking -------------------------------------------------------------

def test_tier_rank_orders_flagship_above_small():
    assert _tier_rank("gpt-5.5") == 0
    assert _tier_rank("gpt-5.5-mini") < 0
    assert _tier_rank("gpt-5.5-nano") < _tier_rank("gpt-5.5-mini")
    assert _tier_rank("qwen-max") > 0
    assert _tier_rank("gemini-pro") > _tier_rank("gemini-flash")


# -- selection ----------------------------------------------------------------

def test_pick_latest_within_family():
    ids = ["gpt-5", "gpt-5.5", "gpt-4.1", "gpt-4o"]
    assert pick_latest_matching(ids, r"^gpt-5") == "gpt-5.5"


def test_pick_prefers_flagship_over_mini_same_version():
    ids = ["gpt-5.5-mini", "gpt-5.5", "gpt-5.5-nano"]
    assert pick_latest_matching(ids, r"^gpt-5") == "gpt-5.5"


def test_pick_latest_opus_lineage():
    ids = ["claude-opus-4-1", "claude-opus-4-8", "claude-sonnet-4-5"]
    assert pick_latest_matching(ids, r"claude-opus-4") == "claude-opus-4-8"


def test_pick_latest_deepseek_vN():
    ids = ["deepseek-v3", "deepseek-v4", "deepseek-chat"]
    assert pick_latest_matching(ids, r"deepseek-v") == "deepseek-v4"


def test_pick_returns_none_when_no_match():
    assert pick_latest_matching(["gpt-4o"], r"^gpt-5") is None


def test_pick_is_null_safe():
    assert pick_latest_matching(None, r"^gpt-5") is None
    assert pick_latest_matching([], r"^gpt-5") is None


def test_gemini_pin_pattern_excludes_non_pro_variants():
    # The committed Gemini pattern must match ONLY 3.1-pro / -preview and never
    # the flash / image / customtools variants the live API also lists.
    pat = defined_provider_specs()["gemini"].model_patterns[0]
    ids = [
        "models/gemini-3.1-pro-preview",
        "models/gemini-3.1-pro",
        "models/gemini-3.1-flash",
        "models/gemini-3.1-pro-image",
        "models/gemini-2.5-pro",
    ]
    picked = pick_latest_matching(ids, pat)
    assert picked in (
        "models/gemini-3.1-pro-preview",
        "models/gemini-3.1-pro",
    ), picked
    assert "flash" not in picked and "image" not in picked, picked


# -- committed config: families rotate, Gemini stays pinned -------------------

def test_config_loads_all_new_fields():
    specs = defined_provider_specs()
    for key in llm_enabled_keys():
        spec = specs[key]
        assert spec.exec_locus, key
        assert spec.specialty, "%s missing specialty" % key
        assert spec.benchmark_profile, "%s missing benchmark_profile" % key
        # frontend_uiux is the EXTREME UI/UX category — every provider must
        # advertise a score for it so lead routing can compare apples-to-apples.
        assert "frontend_uiux" in spec.benchmark_profile, key
        for cat, val in spec.benchmark_profile.items():
            assert 0.0 <= float(val) <= 1.0, (key, cat, val)


def test_config_frontier_defaults_and_rotation():
    specs = defined_provider_specs()
    assert specs["openai"].default_model == "gpt-5.5"
    assert any(p.startswith("^gpt-5") for p in specs["openai"].model_patterns)
    assert specs["anthropic"].default_model == "claude-opus-4-8"
    assert specs["anthropic"].alias_env == "ANTHROPIC_OPUS_ALIAS"
    assert any("opus" in p for p in specs["anthropic"].model_patterns)
    assert specs["grok"].default_model == "grok-4.3"
    assert "grok-4" in specs["grok"].model_patterns


def test_gemini_remains_pinned():
    spec = defined_provider_specs()["gemini"]
    assert spec.default_model == "models/gemini-3.1-pro-preview"
    # Exactly the single anchored pin pattern — no broadened lineage rotation.
    assert spec.model_patterns == [r"gemini-3\.1-pro(-preview)?$"], spec.model_patterns


def test_new_providers_are_openai_compatible_provider_hosted():
    specs = defined_provider_specs()
    for key in ("deepseek", "mistral", "qwen"):
        spec = specs[key]
        assert spec.adapter == "openai_chat", key
        assert spec.exec_locus == "provider_hosted", key
        assert spec.default_base_url, key
        assert spec.secrets, "%s must declare its secret NAME" % key
        assert spec.model_patterns, key


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
