"""Tests for benchmark-aware lead routing (T003).

Benchmark routing is a governed *tie-breaker*: it deterministically classifies a
prompt into a category, picks a specialist LEAD from the AVAILABLE providers by
their declared benchmark_profile, gives that lead a stronger hint, and conveys a
CAPPED, guarded weighting signal to the governed judge. It must NEVER:
  * drop a non-lead reply,
  * skip the governed judge merge,
  * lift a lead whose own reply fails the compliance / hallucination floors.

These tests pin down all three: the deterministic classifier, the argmax lead
selection, the guard logic, and the end-to-end call_team wiring (lead hint +
debate tag + surfaced metadata) — all offline, with no network/provider calls.

Runnable two ways:
  * ``python tests/test_benchmark_routing.py``  (standalone, exits non-zero on failure)
  * ``pytest tests/test_benchmark_routing.py``   (test_* functions; no plugins needed)
"""
import asyncio
import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Keep the swarm offline and quiet: stub mode + silence the benign google
# generativeai import FutureWarning so test output stays readable.
os.environ.setdefault("LLM_PROVIDER", "stub")
warnings.filterwarnings("ignore")

from src.swarm import benchmark_routing as br
from src.swarm.benchmark_routing import (
    classify_category, select_lead, route, BenchmarkRouting, CATEGORIES,
)


# ───────────────────────── classify_category ─────────────────────────

def test_classify_frontend_uiux_from_keywords():
    cat, sig = classify_category(
        "Build a responsive UI with Tailwind and great UX and accessibility", "general"
    )
    assert cat == "frontend_uiux", (cat, sig)
    # signal lists matched tokens, never a raw prompt excerpt
    assert "matched:" in sig, sig
    assert "Build a responsive" not in sig, sig


def test_classify_coding():
    cat, _ = classify_category("Refactor this python function and fix the bug", "general")
    assert cat == "coding", cat


def test_classify_security():
    cat, _ = classify_category("Find the security vulnerability and the xss issue", "general")
    assert cat == "security", cat


def test_classify_math():
    cat, _ = classify_category("Prove this theorem using algebra and a matrix", "general")
    assert cat == "math", cat


def test_classify_multilingual():
    cat, _ = classify_category("Please translate this text for me", "general")
    assert cat == "multilingual", cat


def test_classify_creative():
    cat, _ = classify_category("Write a poem, be creative with the narrative", "general")
    assert cat == "creative", cat


def test_classify_long_context():
    cat, _ = classify_category("Summarize this long document across files", "general")
    assert cat == "long_context", cat


def test_classify_agentic():
    cat, _ = classify_category("Build an autonomous agent workflow pipeline", "general")
    assert cat == "agentic", cat


def test_classify_reasoning():
    cat, _ = classify_category("Analyze the trade-off and explain why", "general")
    assert cat == "reasoning", cat


def test_classify_general_default():
    cat, sig = classify_category("hello there friend", "general")
    assert cat == "general", cat
    assert "default general" in sig, sig


def test_short_token_does_not_match_inside_word():
    # 'ui' must NOT match inside 'build'/'fluid'; this prompt is pure coding.
    cat, _ = classify_category("Build and compile the rust library quickly", "general")
    assert cat == "coding", cat


def test_domain_hint_breaks_a_keywordless_prompt():
    # No category keywords, but the operator-chosen domain nudges the category.
    cat, _ = classify_category("make it nicer please", "design")
    assert cat == "frontend_uiux", cat
    cat2, _ = classify_category("do the thing", "engineering")
    assert cat2 == "coding", cat2


def test_classify_is_bounded_and_safe():
    # A pathologically long prompt must not error and must still classify.
    cat, _ = classify_category("ui " * 5000, "general")
    assert cat == "frontend_uiux", cat


# ───────────────────────── select_lead ─────────────────────────

def test_select_lead_argmax_with_general_fallback():
    cands = [
        ("a", "sa", {"coding": 0.90, "general": 0.50}),
        ("b", "sb", {"coding": 0.95, "general": 0.60}),
        ("c", "sc", {"general": 0.70}),  # no 'coding' -> falls back to general
    ]
    lead, specialty, score, scores = select_lead("coding", cands)
    assert lead == "b", lead
    assert specialty == "sb", specialty
    assert score == 0.95, score
    assert scores["c"] == 0.70, scores  # general fallback used for c


def test_select_lead_tie_resolves_to_declared_order():
    cands = [("a", "sa", {"x": 0.5}), ("b", "sb", {"x": 0.5})]
    lead, _, _, _ = select_lead("x", cands)
    assert lead == "a", lead


def test_select_lead_empty_candidates():
    assert select_lead("coding", []) == (None, "", None, {})


def test_route_combines_classify_and_select():
    cands = [
        ("alpha", "build", {"frontend_uiux": 0.90, "general": 0.8}),
        ("beta", "ux critique", {"frontend_uiux": 0.94, "general": 0.85}),
        ("gamma", "misc", {"frontend_uiux": 0.80, "general": 0.7}),
    ]
    r = route("Design a polished responsive dashboard UI with great UX", "design", cands)
    assert r.category == "frontend_uiux", r.category
    assert r.lead_provider == "beta", r.lead_provider
    assert r.lead_specialty == "ux critique", r.lead_specialty
    d = r.to_dict()
    for k in ("category", "lead_provider", "lead_specialty", "scores_considered",
              "bias_applied", "bias_reason", "bias_delta", "classification_signal"):
        assert k in d, k


def test_categories_cover_frontend_uiux():
    assert "frontend_uiux" in CATEGORIES
    assert "general" in CATEGORIES


# ───────────────────────── BenchmarkRouting transitions ─────────────────────────

def test_routing_bias_transitions():
    r = BenchmarkRouting(category="coding", classification_signal="x")
    assert r.bias_applied is False and r.bias_reason == "not_evaluated"
    r.mark_bias_applied(0.07)
    assert r.bias_applied is True and r.bias_delta == 0.07
    r.mark_bias_suppressed("floor")
    assert r.bias_applied is False and r.bias_delta == 0.0 and r.bias_reason == "floor"


# ───────────────────────── _benchmark_bias guard logic ─────────────────────────

def _llm():
    from src.swarm.llm_multi import MultiProviderLLM
    return MultiProviderLLM()


def _reply(provider="x", ok=True, text="answer", cs=95, hr=0.1):
    from src.swarm.llm_multi import ProviderReply
    r = ProviderReply(provider, provider + "-m", text, ok)
    r.compliance_score = cs
    r.hallucination_risk = hr
    return r


def test_bias_applied_when_guards_pass():
    m = _llm()
    applied, delta, _ = m._benchmark_bias(_reply(cs=90, hr=0.1), base_weight=1.0)
    assert applied is True
    assert abs(delta - 0.10) < 1e-9, delta  # min(0.10, 1.0*0.10)


def test_bias_delta_is_proportional_and_capped():
    m = _llm()
    # small base weight -> proportional (0.5 * 0.10 = 0.05)
    _, delta_small, _ = m._benchmark_bias(_reply(), base_weight=0.5)
    assert abs(delta_small - 0.05) < 1e-9, delta_small
    # large base weight -> capped at 0.10
    _, delta_big, _ = m._benchmark_bias(_reply(), base_weight=5.0)
    assert abs(delta_big - 0.10) < 1e-9, delta_big


def test_bias_suppressed_below_compliance_floor():
    m = _llm()
    applied, delta, reason = m._benchmark_bias(_reply(cs=79), base_weight=1.0)
    assert applied is False and delta == 0.0
    assert "compliance" in reason, reason


def test_bias_suppressed_high_hallucination():
    m = _llm()
    # HR 0.6 -> HIGH tier; 0.8 -> CRITICAL. Both must suppress.
    for hr in (0.6, 0.8):
        applied, _, reason = m._benchmark_bias(_reply(cs=95, hr=hr), base_weight=1.0)
        assert applied is False, hr
        assert "hallucination" in reason, reason


def test_bias_suppressed_when_lead_failed_or_empty():
    m = _llm()
    applied, _, _ = m._benchmark_bias(_reply(ok=False, text=""), base_weight=1.0)
    assert applied is False
    applied2, _, _ = m._benchmark_bias(_reply(ok=True, text="   "), base_weight=1.0)
    assert applied2 is False


# ───────────────────────── call_team end-to-end wiring ─────────────────────────

class _FakeSpec:
    def __init__(self, specialty, profile):
        self.specialty = specialty
        self.benchmark_profile = profile


class _FakeAdapter:
    def __init__(self, key, specialty, profile, role_hint="base hint"):
        self.key = key
        self.model = key + "-model"
        self.role_hint = role_hint
        self.spec = _FakeSpec(specialty, profile)

    def is_available(self):
        return True


def _wire_team(cs_map=None, hr_map=None):
    """A MultiProviderLLM with fake adapters and no network. Returns (m, captured_hints)."""
    from src.swarm.llm_multi import ProviderReply
    import src.promptopt.runtime as rt

    cs_map = cs_map or {}
    hr_map = hr_map or {}
    m = _llm()

    fakes = [
        _FakeAdapter("alpha", "UX build", {"frontend_uiux": 0.90, "general": 0.8}),
        _FakeAdapter("beta", "visual/UX critique", {"frontend_uiux": 0.94, "general": 0.85}),
        _FakeAdapter("gamma", "misc", {"frontend_uiux": 0.80, "general": 0.7}),
    ]
    m._available_adapters = lambda: fakes

    async def _noop():
        return None
    m._resolve_once = _noop

    async def _can_call(key):
        return True
    m.circuit_breaker.can_call = _can_call

    captured = {}

    async def _fake_call(adapter, system, user, hint):
        captured[adapter.key] = hint
        return ProviderReply(adapter.key, adapter.model, f"answer from {adapter.key}", True)
    m._call = _fake_call

    def _score(replies):
        for r in replies:
            r.compliance_score = cs_map.get(r.provider, 100)
            r.hallucination_risk = hr_map.get(r.provider, 0.0)
        return replies
    m.score_replies = _score

    async def _fake_run_judge(user, debate, critical_warning, budget):
        # Echo the debate so the test can inspect the per-provider headers/tags.
        return debate
    rt.run_judge = _fake_run_judge

    return m, captured


def test_call_team_routes_lead_and_surfaces_metadata():
    m, captured = _wire_team()
    out = asyncio.run(m.call_team(
        "sys", "Design a polished responsive dashboard UI with great UX", domain="design"
    ))
    routing = out["benchmark_routing"]
    assert routing["category"] == "frontend_uiux", routing
    assert routing["lead_provider"] == "beta", routing  # highest frontend_uiux
    assert routing["bias_applied"] is True, routing
    assert routing["bias_delta"] > 0, routing
    # side channel mirrors the returned metadata
    assert m.last_benchmark_routing == routing
    # lead got a stronger specialization hint mentioning its specialty
    assert "LEAD" in captured["beta"], captured["beta"]
    assert "visual/UX critique" in captured["beta"], captured["beta"]
    # non-lead kept its plain base hint
    assert captured["alpha"] == "base hint", captured["alpha"]
    # the governed judge saw the capped lead tag, and NO non-lead was dropped
    merged = out["merged"]
    assert "[BENCHMARK-LEAD:frontend_uiux" in merged, merged
    for key in ("alpha", "beta", "gamma"):
        assert f"answer from {key}" in merged, (key, merged)


def test_call_team_suppresses_bias_when_lead_below_floor():
    # Lead 'beta' returns a low-compliance reply: bias must be suppressed but the
    # lead is still merged (never dropped) and the judge still runs.
    m, captured = _wire_team(cs_map={"beta": 70})
    out = asyncio.run(m.call_team(
        "sys", "Design a responsive UI with great UX", domain="design"
    ))
    routing = out["benchmark_routing"]
    assert routing["lead_provider"] == "beta", routing
    assert routing["bias_applied"] is False, routing
    assert "compliance" in routing["bias_reason"], routing
    merged = out["merged"]
    assert "[BENCHMARK-LEAD" not in merged, merged
    assert "answer from beta" in merged, merged  # not dropped


# ───────────────────────── trajectory momentum bias ─────────────────────────

def _fresh_trajectory_store():
    """Reset the trajectory router singleton so each test is isolated."""
    import src.swarm.trajectory_router as tr
    tr._STORE = None
    return tr


def test_route_without_session_has_trajectory_flag_false():
    cands = [("a", "sa", {"general": 0.5})]
    r = route("hello there friend", "general", cands)
    assert r.trajectory_bias_applied is False
    assert r.to_dict()["trajectory_bias_applied"] is False


def test_route_unknown_session_is_failsoft_no_bias():
    _fresh_trajectory_store()
    cands = [("a", "sa", {"general": 0.5})]
    # session_id provided but never recorded -> no momentum -> inert, no error.
    r = route("hello there friend", "general", cands, session_id="never-seen")
    assert r.category == "general", r.category
    assert r.trajectory_bias_applied is False
    assert r.to_dict()["trajectory_bias_applied"] is False


def test_momentum_tips_ambiguous_prompt_toward_conversation():
    tr = _fresh_trajectory_store()
    sid = "sess-coding"
    # Build a coding-heavy conversation history.
    for txt in (
        "Refactor this python function and fix the bug in the algorithm",
        "Now add a unit test and fix the compile error in the class method",
        "Debug the exception in the api endpoint code",
    ):
        tr.record_turn(sid, txt)

    cands = [
        ("coder", "code", {"coding": 0.95, "general": 0.5}),
        ("misc", "general", {"general": 0.6}),
    ]
    # Keywordless follow-up: base classify -> general. Momentum should tip it
    # toward 'coding' (the conversation's dominant category) and pick the coder.
    r = route("do that again please", "general", cands, session_id=sid)
    assert r.trajectory_bias_applied is True, r.to_dict()
    assert r.category == "coding", r.to_dict()
    assert r.lead_provider == "coder", r.to_dict()
    assert "trajectory_bias=" in r.classification_signal, r.classification_signal


def test_momentum_never_overrides_strong_base_signal():
    tr = _fresh_trajectory_store()
    sid = "sess-mixed"
    # Conversation drifting toward security...
    for txt in (
        "Find the security vulnerability and the exploit and the xss injection",
        "Threat model the auth bypass and harden the attack surface owasp",
    ):
        tr.record_turn(sid, txt)

    cands = [
        ("coder", "code", {"coding": 0.95, "security": 0.4, "general": 0.5}),
        ("sec", "security", {"security": 0.95, "coding": 0.3, "general": 0.5}),
    ]
    # Strong coding prompt (many keyword hits) must NOT be flipped to security
    # by momentum — the deterministic keyword lead wins.
    strong_coding = (
        "Refactor this python function, fix the bug, write a unit test, "
        "implement the api endpoint and fix the compile error in the class method"
    )
    r = route(strong_coding, "general", cands, session_id=sid)
    assert r.category == "coding", r.to_dict()
    assert r.lead_provider == "coder", r.to_dict()
    # momentum was still evaluated (flag true) even though it changed nothing.
    assert r.trajectory_bias_applied is True, r.to_dict()


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
