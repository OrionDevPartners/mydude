---
name: Swarm-result UI e2e without a live run
description: How to e2e-verify governed-swarm result rendering without triggering an expensive live LLM swarm run
---

A live WaveOrchestrator run fans out `AGENTS_PER_WAVE` (default 60) × `MAX_WAVES`
(default 4) **real** provider calls — on the order of ~240 LLM calls per single
run. Triggering that from a Playwright e2e just to confirm result-panel rendering
is far too slow, expensive, and irresponsible.

**Why:** `orchestrator._build_jobs` loops `range(AGENTS_PER_WAVE)` per wave and
each job is a real `_call_worker` against the live provider swarm.

**How to apply:** to e2e-verify any SPA surface that renders a *completed* task's
governed scores, insert a `TaskRun` row directly instead of running the swarm:
- `status='completed'`, `result` = JSON envelope (include a `SYNTHESIS` key so the
  main answer renders), `provider_scores` = JSON matching `service.normalize_scores`
  output (the compact `compliance` / `hallucination_risk` / `jurisdiction` plus the
  `benchmark` block `{category, lead_provider, lead_specialty, classification_signal,
  bias_applied}`).
- Navigate to `/tasks/<id>` (TaskDetail). The Dashboard result panel and TaskDetail
  share the SAME strip component + data shape, so the injected-row path exercises
  both renderers.

Dev login gotcha: `admin`/`admin` only works if the admin seed ran on first boot
(`seed_admin_user`), which it often hasn't on an established dev DB. For e2e, create
a throwaway `User` via `src.web.auth.hash_password(...)` (set `is_active`/`is_admin`)
and delete it afterward. Also delete any injected `TaskRun` rows when done —
credential hygiene, and never leave fake governed runs masquerading as real
(governance pillar 1).
