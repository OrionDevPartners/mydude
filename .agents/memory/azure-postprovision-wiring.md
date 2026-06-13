---
name: Azure post-provision wiring (MyDude)
description: How the deployed Azure stack is wired for app use, and why it must run from inside the VNet.
---

# Azure post-provision wiring

The deployed `mydude` stack (Postgres flexible, Key Vault, Cosmos, AOAI) is
**private by design**: public network access Disabled + private endpoints,
enforced by the RG-scoped `mydude-deny-public-network` policy. From the Replit
workspace (outside the VNet) every data plane is unreachable — Postgres has no
public DNS and 5432 is closed; KV/Cosmos/AOAI accept TLS but reject the data
plane. This is correct, not a bug.

**Rule:** post-provision wiring (set KV secrets, run migrator, seed Cosmos,
verify reachability) MUST run from inside the VNet (jump box / VNet-integrated
container). Do NOT execute or "verify live" from the workspace; deliver runnable,
fail-loud scripts instead.

**Why:** services are private-only; the only way to reach them is from within the
VNet, so unverifiable-from-here is the expected state.

**How to apply:**
- Wiring lives in `infra/mydude/local/`: `azure_common.py` (shared seam) +
  `populate_keyvault.py`, `seed_cosmos.py`, `dataplane_doctor.py` CLIs. Migrator
  gains `--from-keyvault`.
- Auth: `DefaultAzureCredential` everywhere (MI on the host, or AZURE_* SP env).
  Cosmos key auth is disabled (AAD only); identity needs Cosmos Built-in Data
  Contributor, KV get/set, Cognitive Services OpenAI User.
- Secret sourcing (pillar #3): KV secret names `agents-home-pg-dsn`,
  `provider-home-pg-dsn`, `bcs-idempotency-key`; `hydrate_env_from_keyvault()`
  maps them to `PG_AGENTS_HOME_DSN`, `PG_PROVIDER_HOME_DSN`, `BCS_LEASE_SECRET`.
- Cosmos containers (`agents_memory`: episodic/vectors/documents) are created by
  `cosmos.bicep`; data-plane scripts VERIFY + probe-write only (creating
  containers needs the management plane, not Data Contributor).
- Adding the SDKs (`azure-keyvault-secrets`, `azure-cosmos`) via `uv add` worked
  (requires-python bounded) — see uv-frozen-sync.md.
