---
name: Jurisdiction routing wiring
description: How MyDude task runs enforce exec_locus pins, the cloud_shift kill switch, and the 5-tier fallback ladder.
---

The routing brain lives in infra/mydude/routing/jurisdiction.py (JurisdictionRouter,
CloudShiftKillSwitch) and is bridged by src/swarm/jurisdiction.py. The live runner
enforces it at the provider-selection layer, not by replacing the router.

**How enforcement works:** MultiProviderLLM._available_adapters() applies a
jurisdiction filter (exec_locus pin + cloud_shift). The orchestrator calls
jurisdiction_metadata() once at the start of run() and pins the swarm via
LLM.apply_jurisdiction(). exec_locus per provider comes from config/providers.toml
([providers.*].exec_locus), NOT a DB.

**Kill switch:** cloud_shift off drops every non-local provider. Operators flip it
from /governance (Jurisdiction tab → POST /api/governance/cloud-shift, auth-gated,
audited via AuditLog command="cloud_shift_toggle"). set_cloud_shift() writes to the
agents_home DB (CloudShiftKillSwitch.set_enabled) when PG_AGENTS_HOME_DSN is set,
else persists an app_settings override (key cloud_shift_override "true"/"false").
Resolution precedence in _resolve_cloud_shift(): agents_home DB (if DSN) → app_settings
override → CLOUD_SHIFT_ENABLED env default → true. **Why:** the operator's deliberate
runtime action must beat the static env default, or the incident kill switch is a lie.
The toggle invalidates the 5s cache so the badge updates immediately. There are
currently NO local-exec providers declared, so disabling correctly resolves to refuse
(tier 5) until one is added — that is honest behavior, not a bug.

**Gotcha:** jurisdiction_metadata() prefers the agents_home router when its module
imports successfully, even with no PG_AGENTS_HOME_DSN — in that case decide() returns
refused/tier-5 because model_team_policy is unreachable. The orchestrator overrides
that with the live team's effective_routing() when a real provider team exists, so the
recorded tier reflects actual provider availability. The env-fallback branch (tier 1
on / tier 4 off) only runs if the infra module import fails.

**Where it surfaces:** result["JURISDICTION"] → persisted into
TaskRun.provider_scores; shown on /governance (cloud_shift + exec_locus distribution)
and in the task-detail report macro.

**Per-request domain selection:** the run endpoints (`/api/tasks/run` and the legacy
Jinja `/tasks/run`) accept `domain`/`team` form fields and thread them into
`orchestrator.run(domain=, team=)`. The curated domain list + `normalize_domain`/
`normalize_team` slug sanitizers are the single source of truth in
`src/swarm/jurisdiction.py` (`JURISDICTION_DOMAINS`); the dashboard payload exposes
it so the SPA selector renders from the backend list (not a hardcoded UI copy).
Domains are intentionally NOT enum-validated — model_team_policy is data-driven, so
operators may configure domains beyond the curated list. The exec_locus/tier only
*visibly* differs per domain when an agents_home model_team_policy is reachable;
without PG_AGENTS_HOME_DSN every domain resolves to the same env fallback.
