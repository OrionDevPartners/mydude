"""Tests for zero-token structural routing surfaced in the task-run pipeline.

The zero-token router (``src/swarm/zero_token_router.py``) decides, before the
LLM swarm runs, whether a goal structurally matches an indexed capability
strongly enough to be dispatched mechanically through the governed broker. This
test guards the wiring that records that decision as ``STRUCTURAL_ROUTING`` so
the TaskDetail "Routing" card and the History zero-token badge have real data.

What it guards:
  * a MISS produces a populated decision dict (dispatched=False) the orchestrator
    attaches to the final result — never a placeholder;
  * a HIT short-circuits the swarm with a compact, valid envelope that carries
    SYNTHESIS + STRUCTURAL_ROUTING;
  * the API serializer (_parse_task) lifts STRUCTURAL_ROUTING onto the
    ``structural_routing`` field the SPA reads.

Fully hermetic: no live LLM provider, no broker dispatch, no DB. The router is
faked; the real TrajectorySession runs (it is offline TF-IDF safe).

Runnable two ways:
  * ``python tests/test_structural_routing.py``
  * ``pytest tests/test_structural_routing.py``
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.pop("REPLIT_DEPLOYMENT", None)

from src.swarm.orchestrator import WaveOrchestrator
from src.swarm.zero_token_router import ZeroTokenResult
import src.swarm.zero_token_router as ztr


class _FakeStats:
    embedding_backend = "tfidf-fallback"


class _FakeRouterMiss:
    threshold = 0.92

    def score_intent(self, intent):
        return [{"capability": "git_status", "score": 0.41}]

    async def async_route(self, intent, *, session_id=None, broker=None):
        return None  # below threshold -> swarm proceeds

    def get_stats(self):
        return _FakeStats()


class _FakeRouterHit:
    threshold = 0.92

    def score_intent(self, intent):
        return [{"capability": "git_status", "score": 0.95}]

    async def async_route(self, intent, *, session_id=None, broker=None):
        return ZeroTokenResult(
            capability="git_status",
            score=0.95,
            threshold=0.92,
            dispatched=True,
            intent_snippet=intent[:200],
            tool_output="working tree clean",
            error=None,
            elapsed_ms=1.0,
        )

    def get_stats(self):
        return _FakeStats()


class _DummyOrch:
    """Minimal stand-in carrying just the attribute the helpers touch."""
    broker = object()


def _eval(router, goal="check git status"):
    orig = ztr.get_router
    ztr.get_router = lambda: router
    try:
        return asyncio.run(
            WaveOrchestrator._evaluate_structural_routing(_DummyOrch(), goal, "sess1234")
        )
    finally:
        ztr.get_router = orig


def test_miss_records_full_decision():
    decision = _eval(_FakeRouterMiss())
    assert decision, "miss must still produce a decision dict (no placeholder)"
    assert decision["dispatched"] is False
    assert decision["eligible"] is False
    assert decision["capability"] == "git_status"
    assert decision["score"] == 0.41
    assert decision["threshold"] == 0.92
    assert "trajectory" in decision
    tj = decision["trajectory"]
    assert "dominant_category" in tj and "dominant_score" in tj and "hazard_hints" in tj
    assert isinstance(tj["hazard_hints"], list)


def test_hit_records_dispatch():
    decision = _eval(_FakeRouterHit())
    assert decision["dispatched"] is True
    assert decision["eligible"] is True
    assert decision["capability"] == "git_status"
    assert decision["score"] == 0.95
    assert decision.get("tool_output") == "working tree clean"


def test_hit_envelope_is_valid_and_carries_routing():
    decision = _eval(_FakeRouterHit())
    env = WaveOrchestrator._build_zero_token_envelope(
        _DummyOrch(), "check git status", decision, {"domain": "general", "team": "default"}
    )
    assert env.get("SYNTHESIS"), "envelope must carry a human-readable synthesis"
    assert "git_status" in env["SYNTHESIS"]
    assert env.get("SYNTHESIS_SOURCE") == "zero_token_router"
    assert env.get("STRUCTURAL_ROUTING") == decision
    assert isinstance(env.get("JURISDICTION"), dict)


def test_router_failure_is_failsafe():
    class _Boom:
        threshold = 0.92

        def score_intent(self, intent):
            raise RuntimeError("index unavailable")

        async def async_route(self, intent, *, session_id=None, broker=None):
            raise RuntimeError("dispatch unavailable")

        def get_stats(self):
            raise RuntimeError("no stats")

    decision = _eval(_Boom())
    # Never raises; dispatched stays False so the full swarm runs.
    assert decision["dispatched"] is False
    assert "error" in decision


def test_parse_task_lifts_structural_routing():
    import json
    from src.web.api.router import _parse_task

    class _Task:
        id = 7
        prompt = "check git status"
        status = "completed"
        result = json.dumps({
            "SYNTHESIS": "done",
            "STRUCTURAL_ROUTING": {"dispatched": True, "capability": "git_status", "score": 0.95},
        })
        provider_scores = None
        execution_time_ms = 12
        actor_user_id = 1
        actor_username = "tester"
        created_at = None

    out = _parse_task(_Task())
    assert out["structural_routing"] == {
        "dispatched": True, "capability": "git_status", "score": 0.95,
    }

    class _TaskNoRouting(_Task):
        result = json.dumps({"SYNTHESIS": "older run"})

    out2 = _parse_task(_TaskNoRouting())
    assert out2["structural_routing"] is None, "older runs must degrade gracefully"


if __name__ == "__main__":
    test_miss_records_full_decision()
    print("PASS: miss records full decision")
    test_hit_records_dispatch()
    print("PASS: hit records dispatch")
    test_hit_envelope_is_valid_and_carries_routing()
    print("PASS: hit envelope valid + carries routing")
    test_router_failure_is_failsafe()
    print("PASS: router failure is fail-safe")
    test_parse_task_lifts_structural_routing()
    print("PASS: _parse_task lifts structural_routing")
    print("ALL TESTS PASSED")
