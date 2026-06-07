---
name: Local LLM provider availability gating
description: How Ollama/MLX local providers stay enabled without poisoning the swarm fanout, and how cloud_shift/exec_locus routing picks them.
---

# Local LLM providers (Ollama / MLX)

Local providers are declared in config/providers.toml with `exec_locus = "local"`
and `secrets = []`. They are in the `llm.enabled` list permanently.

**Rule:** a local provider with no secrets would otherwise always look
"available" (base `is_available()` only checks secrets + client built). To stop a
dead local box from being added to the swarm fanout and failing every call, the
local adapter base (`_LocalOpenAICompatAdapter`) overrides `is_available()` with a
fast TCP socket probe of its base_url host:port. Down server => not available.

**Why:** without the probe, enabling local providers in a cloud deployment (no
Ollama/MLX running) makes every task waste retries on connection errors.

**Routing:** the swarm filters adapters through
`src.swarm.jurisdiction.permitted_provider_keys()`:
- `EXEC_LOCUS_PIN` env (e.g. `local`) hard-restricts to matching exec_locus
- `cloud_shift=false` (CLOUD_SHIFT_ENABLED env or agents_home) restricts to `local`
- otherwise all providers allowed.
The infra router (infra/mydude/routing/jurisdiction.py) `_local_provider_candidates()`
reads the same config local providers for the local_degraded tier when there's no
policy DB, so it degrades to local instead of refusing.

**Model resolution:** local adapters prefer the installed model from
`~/.mydude/local/model_registry.yaml` (read via src/providers/local_registry.py,
needs pyyaml) over the static config default; both degrade gracefully when the
file/registry is absent.
