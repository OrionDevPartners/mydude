---
name: Provider-agnostic architecture (three-layer)
description: How LLM providers are abstracted via env_1 config + adapters + env_2 secrets, and why the boot handshake fails-fast only on declared `required` providers.
---

# Provider & environment agnostic architecture

Three layers, strictly separated:
- **Code** — `src/providers/` (base.py interface, adapters.py, registry.py) +
  call sites (`swarm/llm_multi.py`, `swarm/orchestrator.py`,
  `swarm/model_resolver.py`). Names NO vendor.
- **env_1** — `config/providers.toml` (committed, no secret values). Maps
  capability→provider, lists `llm.enabled` (swarm members) and `llm.required`
  (fail-fast), and per-provider adapter/secret-names/model/role_hint/patterns.
- **env_2** — Replit Secrets / the vault sync target, read ONLY through
  `src/providers/secrets.py`.

Add a provider = 1 adapter class registered in `registry.py` + 1
`[providers.<key>]` block in env_1. Swap active provider = edit `llm.enabled`.

## Boot handshake: fail-fast vs empty-boot
**Rule:** `src/providers/handshake.py` (called in `app.py` startup, fatal on
raise) hard-fails ONLY for providers listed in `llm.required`. `enabled`
providers without keys degrade gracefully at runtime.

**Why:** this app is a runtime credential vault — it MUST boot with an empty
vault so users can add keys through the UI. A blanket "all enabled providers
need secrets at boot" rule would brick first boot (can't reach the UI to add
keys) and break the dev environment (`.replit` ships `LLM_PROVIDER=multi` with
no keys). `required` (default `[]`) is the explicit fail-fast knob operators set
once their secrets are in place.

**How to apply:** to enforce a secret exists at boot, add the provider key to
`llm.required` in `config/providers.toml`. Don't reintroduce hardcoded vendor
strings in call sites — resolve through `providers.config` + the adapter
registry. The handshake also validates config integrity (unknown adapter,
undefined provider, required∉enabled).
