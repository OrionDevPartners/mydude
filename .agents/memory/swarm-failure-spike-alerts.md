---
name: Swarm failure-spike alerting
description: How recoverable swarm failure counters turn a burst into a SentinelEvent, and the persistence/reset rules that keep it from spamming or going silent.
---

# Swarm failure-spike alerting

Recoverable-but-noteworthy swarm failures (run-index persistence, governance-
proposal raising) call `error_metrics.record_failure(key)` — the governed entry
point that increments the durable cumulative counter AND raises a `SentinelEvent`
when failures burst past a threshold within a window. Burst detection is a
fixed-window counter + cooldown.

## Rules (apply when adding any new failure-spike alert)

- **Persist window/cooldown state, never in process memory.** This platform
  restarts workers frequently; in-memory burst state would re-arm and re-alert
  on every restart during an ongoing outage. State lives in `app_settings`
  (same lesson as the local-node-offline alerts).
  **Why:** one outage must raise ~one alert, not one per restart.
- **Re-arm after firing + cooldown gate.** After an alert fires, reset the
  window so the next alert needs *both* a fresh burst AND the cooldown to
  elapse → at most one alert per cooldown, never one per failure.
- **Reset must clear alert state in lockstep.** When an operator resets a
  counter, also clear its window/cooldown keys, or a stale cooldown silently
  suppresses the alert for a *new* outage that starts right after the reset.
  **How to apply:** only counters registered in `_METRIC_ALERT_META` alert;
  add new ones there and they auto-participate in both alerting and reset-clear.
- **Config is env-driven** (`SWARM_FAILURE_ALERT_THRESHOLD/_WINDOW_SECONDS/
  _COOLDOWN_SECONDS`, mirrored from the settings store); `threshold<=0`
  disables. Best-effort throughout — the spike check must never break the
  caller, mirroring the rest of `error_metrics`.
- **No new API/UI needed:** `/api/governance` already maps `SentinelEvent`
  (`alert_type->rule`, `description(+recommended_action)->detail`), so new
  alert_type values surface in the React Governance feed automatically.
