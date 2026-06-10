---
name: Governed degraded-fallback rule
description: How any "primary governed path failed" fallback must behave so it never bypasses the live/approved prompt or emits ungoverned output.
---

# Degraded fallbacks must not bypass governance

When a governed inference path (e.g. the DSPy judge in `_judge_merge`) fails at
runtime, the fallback must NOT:
- use a hardcoded/duplicated copy of the prompt — that copy silently diverges from
  the live version, and if an evolved version was promoted, runtime would serve the
  stale text (an evolved-prompt bypass); and
- dump raw ungoverned model/provider output as if it were a synthesized answer.

Instead the fallback must:
1. re-load the LIVE governance-approved instructions (`store.get_live_instructions`)
   and run THOSE via a raw provider call — so the approved/evolved prompt is honored
   even in degraded mode;
2. mark the output explicitly degraded/unverified (a visible banner) so callers/users
   know full governance scoring did not complete;
3. record the run as a `status="degraded"` trace (NOT `"ok"`) so it is audited but
   excluded from the optimizer trainset (`count_usable_traces`/`load_usable_traces`
   filter `status=="ok"`); and
4. if no provider is reachable at all, fail loud in the expected output format rather
   than returning raw content.

**Why:** governance pillars 1 (no placeholders / fail-loud) & 4 (no ungoverned model
output reaches a user). A code review blocked the originally-planned "keep the
hardcoded prompt + return raw debate" fallback for exactly these reasons.

**How to apply:** whenever you add a fallback around a governed/versioned prompt
program, route it through the live instructions + mark + audit, never a static copy.
