"""Tests for the dashboard AI Task Runner pipeline (POST /api/tasks/run).

Exercises the run -> persist -> retrieve flow end to end *minus* a live LLM
provider: the WaveOrchestrator is stubbed with a representative governed-swarm
output so we can assert the API layer normalizes it into the compact shape the
React dashboard renders.

What this guards against (the bug this test was written for): the orchestrator
emits COMPLIANCE_SCORES as a list of per-agent dicts and HALLUCINATION_RISK as a
{average,trend,tier} dict, but the dashboard's ResultPanel/TaskDetail only render
score bars when scores.compliance / scores.hallucination_risk are *numbers*. The
run endpoint must therefore collapse them to 0..1 floats and JURISDICTION to a
short string, and the swarm must surface a human-readable SYNTHESIS answer key.

Auth is satisfied via DEV_AUTH_BYPASS so no login round-trip is needed. A live
PostgreSQL database (the Replit built-in) is required since TaskRun rows are
persisted and read back.

Runnable two ways:
  * ``python tests/test_task_run_pipeline.py``  (standalone, exits non-zero on failure)
  * ``pytest tests/test_task_run_pipeline.py``   (test_* functions; no plugins needed)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DEV_AUTH_BYPASS", "1")
os.environ.pop("REPLIT_DEPLOYMENT", None)

from fastapi import FastAPI
from fastapi.testclient import TestClient


# A representative WaveOrchestrator.run() output: list/dict score shapes exactly
# as the real orchestrator emits them, so the test fails if the API stops
# normalizing them.
STUB_RESULT = {
    "SYNTHESIS": "Goal: draft a proposal\n\nKey findings:\n\u2022 finding one\n\u2022 finding two",
    "GOAL": "draft a proposal",
    "FACTS": ["finding one", "finding two"],
    "DECISIONS": ["go with option A"],
    "NEXT_TASKS": ["send to client"],
    "RISKS": ["timeline risk"],
    "COMPLIANCE_SCORES": [
        {"agent": "W0-A0", "score": 82, "tier": "good"},
        {"agent": "W0-A1", "score": 90, "tier": "excellent"},
        {"agent": "W0-A2", "score": None},  # malformed entry must be skipped
    ],
    "HALLUCINATION_RISK": {"average": 0.123, "trend": "flat", "tier": "LOW"},
    "JURISDICTION": {"domain": "finance", "team": "us-east", "exec_locus": None},
}


async def _stub_run(self, prompt):  # noqa: ANN001
    return dict(STUB_RESULT)


def _client_and_patches(monkeypatch):
    """Mount the /api router and stub guards + orchestrator. Returns TestClient."""
    import src.web.routes_tasks as rt
    import src.swarm.orchestrator as orch
    from src.web.api.router import router as api_router

    # Bypass the "needs a real provider key" guards (we stub the swarm itself).
    monkeypatch.setattr(rt, "_has_active_keys", lambda: True)
    monkeypatch.setattr(rt, "_llm_providers_available", lambda: True)
    # Reset the shared module-level rate limiter so repeated test runs don't 429.
    rt._run_limiter._events.clear()
    # Stub the swarm so no provider/network is touched.
    monkeypatch.setattr(orch.WaveOrchestrator, "run", _stub_run)

    app = FastAPI()
    app.include_router(api_router)
    return TestClient(app)


def _run_and_fetch(client):
    resp = client.post("/api/tasks/run", data={"prompt": "draft a proposal"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    task_id = body["task_id"]

    detail = client.get(f"/api/tasks/{task_id}")
    assert detail.status_code == 200, detail.text
    return detail.json()


def _check(task):
    assert task["status"] == "completed", task
    scores = task["scores"]
    assert scores is not None, "scores must be populated"

    # Compliance: mean of 82 and 90 (None skipped) normalized to 0..1 -> 0.86
    assert isinstance(scores["compliance"], (int, float)), scores
    assert abs(scores["compliance"] - 0.86) < 1e-6, scores

    # Hallucination risk: the dict's average, surfaced as a bare number
    assert isinstance(scores["hallucination_risk"], (int, float)), scores
    assert abs(scores["hallucination_risk"] - 0.123) < 1e-6, scores

    # Jurisdiction: collapsed to a short display string, never an object
    assert scores["jurisdiction"] == "finance \u00b7 us-east", scores

    # The dashboard reads parsed.SYNTHESIS as the headline answer text
    parsed = task["parsed"]
    assert parsed and parsed.get("SYNTHESIS"), parsed
    assert "Key findings" in parsed["SYNTHESIS"], parsed


def test_run_task_normalizes_scores_and_surfaces_synthesis(monkeypatch):
    client = _client_and_patches(monkeypatch)
    task = _run_and_fetch(client)
    _check(task)


def test_run_task_rejects_empty_prompt(monkeypatch):
    client = _client_and_patches(monkeypatch)
    resp = client.post("/api/tasks/run", data={"prompt": "   "})
    assert resp.status_code == 400, resp.text


if __name__ == "__main__":
    class _MP:
        """Minimal monkeypatch shim for standalone execution."""
        def __init__(self):
            self._undo = []

        def setattr(self, target, name, value=None):
            if value is None:
                # support setattr(obj, value) style not used here
                raise ValueError("use setattr(target, name, value)")
            old = getattr(target, name)
            self._undo.append((target, name, old))
            setattr(target, name, value)

        def undo(self):
            for target, name, old in reversed(self._undo):
                setattr(target, name, old)
            self._undo.clear()

    mp = _MP()
    try:
        c = _client_and_patches(mp)
        _check(_run_and_fetch(c))
        print("PASS: scores normalized + SYNTHESIS surfaced")
        r = c.post("/api/tasks/run", data={"prompt": "   "})
        assert r.status_code == 400, r.text
        print("PASS: empty prompt rejected")
    finally:
        mp.undo()
    print("ALL TESTS PASSED")
