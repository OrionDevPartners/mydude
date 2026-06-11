---
name: Mesh local-node offline alerting
description: How the background HealthMonitor raises SentinelEvents when a local (Ollama/MLX) model node drops, and the SPA governance field-mapping gotcha.
---

# Proactive alerts for offline Mesh local nodes

The background `HealthMonitor` (singleton via `get_health_monitor()`, started in
`app.py` startup, interval `HEALTH_MONITOR_INTERVAL` default 120s) probes local
(`exec_locus=local`) provider nodes each tick by reusing
`provider_exec_locus_distribution()` (which does the live TCP probe). When a node
flips to unreachable it writes a `SentinelEvent(alert_type="local_node_offline")`.

**Transition + dedup rules (two layers, both matter):**
- In-memory `_previous_local_status` raises only on up→down (and first-seen-down,
  matching the existing `_alert_on_critical` pattern), never every tick while down.
- DB dedup keyed on a stable `alert_id = f"LOCAL-OFFLINE-{provider}"`: skip if an
  **unacknowledged** event with that id already exists. **Why:** in-memory state
  resets on every process restart, so without the DB guard each restart piles up a
  duplicate open alert for a node that was already down. After an operator acks
  (post-recovery), a later drop raises a fresh alert.
- Severity: `high` when no local node is still up (no local fallback left), else
  `medium`.

**SPA governance field-mapping gotcha:** the live UI is the React SPA hitting
`/api/governance` (NOT the Jinja `/governance` route). That endpoint maps
`SentinelEvent` into the frontend `Alert` shape, but the model columns are
`alert_type` / `description` / `recommended_action` — there is NO `rule` or
`detail` column. The endpoint must map `rule<-alert_type`,
`detail<-description (+ recommended_action)`, or it 500s the moment any
SentinelEvent row exists. The Jinja route uses the real column names directly.

**Latent (not yet live):** the same `/api/governance` ledger mapping reads
`l.agent_role/provider/score/detail` which also don't exist on
`PerformanceLedgerEntry` — harmless today only because nothing ever inserts a
ledger row (empty list → comprehension never touches the attrs). It will 500 the
whole endpoint if ledger rows ever get created.
