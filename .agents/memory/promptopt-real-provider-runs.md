---
name: Prompt-opt runs against a live LLM provider
description: Operational lessons for running MIPROv2+GEPA self-evolution against a real provider (timeouts, deepcopy, valset noise, completion-only persistence, budget levers).
---

Running the self-evolving prompt engine (MIPROv2 then GEPA) against a REAL provider
(not a DummyLM) surfaced several non-obvious constraints:

- **Bounded timeout + fail-loud on every optimizer LM call.** Optimization fires many
  sequential provider calls; one stalled call (e.g. a gRPC stream that never returns)
  hangs the whole in-process daemon run forever, with no error and no persisted result.
  Enforce a real request timeout at the adapter (the layer that owns the network call)
  AND a backstop timeout in the LM bridge's forward(), with a bounded retry that resets
  the client. Never cap max_tokens as a timeout substitute — it truncates a thinking
  model's output.
- **The LM adapter must be deepcopy-safe.** MIPRO/GEPA deepcopy the student program
  (and its bound LM). If the LM holds non-copyable handles (locks, live SDK clients),
  the optimizer crashes immediately. Make clients lazy/recreatable so a deepcopy carries
  no live handle.
- **Candidates persist only at completion.** A run that dies mid-flight (app restart,
  container recycle) writes nothing — base/best/candidates are saved in one final
  transaction. Size the run to finish before any environment recycle.
- **Tiny valsets give noisy scores.** The judge metric is itself an LLM and is
  non-deterministic, so the SAME compiled program re-evaluates to different scores across
  passes (saw a MIPRO trial 0.68 re-eval to 0.59 on a 2-example valset; GEPA seeded from
  the same program scored higher with identical instructions). Don't over-read a single
  small-valset delta — prefer valset >=4 for a stable signal; expect deltas to swing on
  valset=2.
- **Promotion is gated.** A winning candidate is never auto-promoted; promote raises a
  governance proposal and the version stays status=candidate / current_version_id
  unchanged until the enactment token is applied.

**Budget levers** (execute_run budgets dict) when a run must fit a short window:
`trace_limit` caps the dataset load (the min-traces gate counts the full pool, not the
capped load, so a small trace_limit still passes the gate), `num_threads` parallelizes
valset evals, `gepa_max_metric_calls` bounds GEPA. The MIPRO trial count is fixed by
auto="light" (=10) and is the dominant wall-time cost; shrinking the valset is the main
MIPRO speed lever.
