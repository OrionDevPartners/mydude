---
name: Evolution loop LLM thesis dispatch
description: How the self-evolution loop generates governed LLM-backed thesis candidates via the swarm, and the constraints that keep it safe.
---

# Evolution loop → governed LLM thesis generation

The thesis generator (`_generate_thesis_candidates` in `src/promptopt/evolution.py`)
augments its heuristic candidates with ONE governed LLM-backed candidate produced
by dispatching the full `WaveOrchestrator` (`src/swarm/orchestrator.py`).

**Rule:** an LLM candidate only enters the pool when a provider is available AND
the swarm run passes the governance gate (not aborted, HR tier not HIGH/CRITICAL,
avg compliance ≥ `EVOLUTION_LLM_MIN_CS`, default 55). Otherwise it returns None and
the loop keeps its heuristic candidates. The downstream sandbox + promotion gate
still apply — no ungoverned output reaches live state.

**Why:** governance pillars 2/4 — provider-agnostic + every inference path governed.
The candidate's payload is LLM-derived (prompt_program: a directive appended to live
instructions; swarm_config/role_composition: a bounded step in the LLM-recommended
direction parsed from the swarm synthesis), but its `score_signal` is derived from
the swarm's own CS/HR so a low-quality run can't win selection.

**How to apply (sync-from-thread pattern):** the evolution loop runs in daemon
threads; `WaveOrchestrator.run` is async. `_run_orchestrator_sync` dispatches the
coroutine onto the shared persistent provider loop from `lm_bridge._persistent_loop()`
via `asyncio.run_coroutine_threadsafe(...).result(timeout=...)`. Do NOT spin up a new
event loop per call — reuse that one loop so lazily-built async adapter clients don't
straddle loops (same constraint lm_bridge documents). Always pass a timeout
(`EVOLUTION_LLM_TIMEOUT`, default 240s) so the loop never hangs on a provider.

Operators can force heuristic-only mode with `EVOLUTION_LLM_THESIS=0`.
