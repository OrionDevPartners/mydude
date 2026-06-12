---
name: Orchestrator output → dashboard shape contract
description: The score/answer shape the React result panel requires vs. what WaveOrchestrator emits.
---

# Orchestrator result → dashboard display contract

The React result panel (`frontend/src/pages/Dashboard.tsx` ResultPanel and
`TaskDetail.tsx`) only renders the score bars when `scores.compliance` and
`scores.hallucination_risk` are **plain numbers (0..1)**, and renders a headline
answer only when `parsed` contains one of `SYNTHESIS|SUMMARY|RESULT|OUTPUT|ANSWER|RESPONSE`.

But `WaveOrchestrator.run()` emits structured governance shapes:
- `COMPLIANCE_SCORES` = list of per-agent dicts `{agent, score(0..100 int), tier, hr, ...}`
- `HALLUCINATION_RISK` = dict `{average, trend, tier}`
- `JURISDICTION` = dict `{domain, team, exec_locus, ...}`
- no single answer-text key by itself.

**Rule:** the run endpoint (`src/web/api/router.py` `api_run_task`) must collapse
those into the display shape before persisting to `TaskRun.provider_scores`:
compliance = mean(per-agent score)/100, hallucination_risk = HR dict's average,
jurisdiction = "domain · team" string. And the swarm exposes a `SYNTHESIS` text
key (built from the handoff) as the headline answer.

**Why:** without this, runs complete but the panel shows raw JSON and no bars —
looks broken even though the swarm worked.

**How to apply:** if you change the orchestrator's `final` dict keys/shapes, or
add a new governance score the dashboard should show, update the normalization in
`api_run_task` in lockstep and keep `tests/test_task_run_pipeline.py` green.
