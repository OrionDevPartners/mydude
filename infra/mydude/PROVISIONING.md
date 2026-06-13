# MyDude Total Stack — Provisioning Guide

## What this is

The MyDude **Azure capacity stack** — Azure-native, fully **private**, deployed into the
**existing** resource group `mydude` (lowercase, `eastus2`).

Authority model:
- **Postgres** — relational / governance / audit / secrets authority (`agents_home` + `provider_home`).
- **Cosmos DB (NoSQL + vector)** — the agent swarm's low-latency working memory (document + vector search).
- **Microsoft Fabric / OneLake** — the large domain-knowledge corpus lakehouse.
- **Azure OpenAI** — private foreground + background (agent-mesh) `gpt-4.1-mini` inference.

Vector search lives in Cosmos DB + Postgres pgvector; the knowledge corpus lives in Fabric / OneLake.
There are no separate app-code Container Apps.

## Authority model (locked)

1. **`agents_home` (Postgres) is the routing / governance authority.**
2. **The BCS promotion gate is the only truth writer** — to the governance ledger (Postgres) and
   the knowledge corpus (Fabric / OneLake staging in ADLS). Single managed identity, idempotency
   keys, lease lock, V1–V7 scope gates.
3. **Every Azure / agent-runtime service is an ADAPTER or PROJECTION, never an authority** — the
   Cosmos agent memory and the AI Foundry managed runtime are tool/runtime scope only.
4. **`exec_locus` must match a domain's pin** before a model can be promoted to that domain.

---

## Deployment model

- **Identity:** `ClientSecretCredential` built from the `AZURE_*` secrets. The service principal is
  **Owner on RG `mydude` ONLY**. It performs **no** subscription- or tenant-scoped operations:
  no RG create, no region change, no subscription-scoped role/policy assignment, no resource-provider
  registration, no support tickets.
- **IaC:** Bicep in `infra/mydude/bicep` (`main.bicep` + `modules/`).
- **Driver:** `infra/mydude/local/deploy.py` (Azure Python SDK, **Incremental** mode). It compiles
  `main.bicep` at deploy time and is idempotent — safe to re-run after a fix.

### deploy.py actions

```bash
python3 infra/mydude/local/deploy.py validate        # ARM validate (no cost)
python3 infra/mydude/local/deploy.py whatif          # what-if preview (no cost)
python3 infra/mydude/local/deploy.py deploy --yes    # BILLABLE create-or-update (add --no-wait to return immediately)
python3 infra/mydude/local/deploy.py status          # poll deployment state + outputs
```

- `pgAdminPassword` is injected from the **`PG_ADMIN_PASSWORD`** secret and `tenantId` from the SP's
  tenant **at deploy time** — never written into `parameters.json`.
- `parameters.json` holds only non-secret params: `location`, `environment`, `fabricSkuName`,
  `fabricAdminMembers`, `fabricEnabled`, `foundryHubEnabled`, AOAI capacities, `cosmosMaxThroughput`.

### Bicep toolchain (local container)

The `bicep` CLI lives at `~/.azure/bin/bicep` and is **wiped on every environment reset**:

```bash
bash .agents/skills/bicep/ensure-bicep.sh        # (re)install the bicep CLI
# manual `bicep build` requires: DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1
```

(`deploy.py` compiles `main.bicep` automatically; only manual builds need the env flag.)

---

## Deployed topology (RG `mydude`, `eastus2`, all private)

**Identities (5 user-assigned):** `mydude-bcs-gate`, `mydude-foundry-agent`,
`mydude-agents-home-db`, `mydude-provider-home-db`, `mydude-readonly`.

**Network:** `mydude-vnet` (`10.10.0.0/16`). Subnets: `mydude-aca-subnet` (`10.10.1.0/24`, delegated
`Microsoft.App/environments`), `mydude-pg-subnet` (`10.10.2.0/24`, delegated PostgreSQL flexibleServers),
`mydude-pe-subnet` (`10.10.3.0/24`). Private endpoints: Key Vault, Storage (`dfs`), Cosmos (`Sql`),
AOAI (`account`). When the Foundry Hub is enabled it also adds private endpoints for the Hub
(`amlworkspace`) and its dedicated storage (`blob`, `file`). Postgres uses its **delegated subnet +
private DNS zone** (`mydude.postgres.database.azure.com`) rather than a private endpoint. Private DNS
zones: `vaultcore`, `dfs`, `documents`, `openai`, `api.azureml.ms`, `notebooks.azure.net`, `blob`,
`file`, + Postgres. Azure Policy `mydude-deny-public-network` (RG-scoped).

**Postgres** — `mydude-pg`: `Standard_D4ds_v5` GeneralPurpose, 128 GB, v16, **HA mode `SameZone`**.
Databases: `agents_home` (routing authority), `provider_home` (candidate cognition + outbox).
> **HA note:** `eastus2` + this subscription's offer **restrict** Multi-Zone (`ZoneRedundant`) HA
> (`MultiAzHaIsOfferRestricted`), so HA runs **`SameZone`** (hot standby in the same AZ, node/instance
> failover). Upgrading to `ZoneRedundant` in place requires a support ticket — a subscription-admin
> step, out of scope for the RG-Owner SP.

**Cosmos DB** — `mydude-cosmos`: NoSQL with vector search, Session consistency, zone-redundant,
Continuous-7-day backup, **key auth disabled (AAD only)**, public access Disabled + PE. Database
`agents_memory` (autoscale max `10000` RU/s). Containers:
- `episodic` — partition `/agentId`, TTL off (episodic agent event log).
- `vectors` — partition `/namespace`, **diskANN** vector index (1536-dim, cosine), **dedicated**
  autoscale `4000` RU/s. *(Vector indexing is rejected on shared-DB throughput, so this container
  provisions its own throughput.)*
- `documents` — partition `/namespace` (raw agent documents / working context).

Data-plane RBAC: `mydude-agents-home-db` = Cosmos Data Contributor; `mydude-readonly` = Cosmos Data Reader.

**Storage** — `mydudestg`: ADLS Gen2 (HNS), `Standard_RAGRS`. Containers: `knowledge-raw`,
`onelake-staging`, `lancedb-l2`, `mlflow-artifacts`, `offline-sync`. RBAC: `mydude-bcs-gate` =
Storage Blob Data Contributor; `mydude-readonly` = Storage Blob Data Reader.

**Key Vault** — `mydude-kv`: standard, 90-day soft-delete + purge protection, public access Disabled + PE.
Access: bcs-gate `get/set/list`; foundry-agent `get/list`; agents-home / provider-home `get`.

**Azure OpenAI** — `mydude-aoai` (kind `OpenAI`, `S0`, public access Disabled, PE + DNS). Deployments:
- `gpt-41-mini` — foreground (interactive), `GlobalStandard`, capacity 250.
- `gpt-41-mini-bg` — background (agent-mesh), `GlobalStandard`, capacity 100.

Both are `gpt-4.1-mini` `2025-04-14`, `NoAutoUpgrade`, `Microsoft.DefaultV2` RAI. The foundry-agent
identity holds **Cognitive Services OpenAI User** (inference only — cannot manage deployments).
> AOAI serializes deployment operations per account; the background deployment depends on the
> foreground one, and both depend on the account's PE/DNS/role assignments, to avoid the
> `AccountProvisioningStateInvalid` ("account in state Accepted") race on Incremental re-runs.

**Monitoring** — `mydude-logs` (Log Analytics), `mydude-appinsights`, `mydude-provider-latency-alert`.

---

## Gated capabilities

### Fabric capacity (`fabricEnabled=false` in the SP parameter file — STILL GATED)

`Microsoft.Fabric/capacities` creation needs AAD/Graph authorization the RG-scoped SP cannot satisfy
("Unable to authorize with Azure Active Directory"), so `parameters.json` (the **SP** deploy's param
file, consumed by `deploy.py`) keeps `fabricEnabled=false` — flipping it true there would make every
SP deploy fail at the Fabric module. The Bicep **default** is `true`, so an AAD-authorized deploy
path (or admin run with no override) provisions it. `fabricSkuName` is `F32` (the target capacity).

Enable it as an admin step:
1. A tenant/Fabric admin (or an SP with the required AAD authorization) creates capacity
   **`mydudefabric`** (`F32`) with the configured `fabricAdminMembers` — either via the portal or by
   running the deploy with `fabricEnabled=true` under an authorized identity.
2. OneLake workspace + lakehouse **items** are created in the Fabric portal/API (SaaS — not ARM).
   Private-link hardening for OneLake is tenant/admin-dependent.

### AI Foundry Hub + Project (`foundryHubEnabled=true` — NOW ENABLED, SP-deployable)

The Hub's required backing surface is now in `foundry.bicep` (all gated on `foundryHubEnabled` so it
is created only when the Hub is on), so the RG-Owner SP can deploy it directly:
- a **dedicated GPv2 NON-HNS** storage account **`mydudefoundrystg`** (`StorageV2`, `isHnsEnabled:false`,
  public access Disabled) with **blob + file** private endpoints/DNS — the shared `mydudestg` is HNS,
  which AML rejects as primary workspace storage;
- the shared **Key Vault** (`mydude-kv`) and **App Insights** (`mydude-appinsights`) wired by resource
  ID (the Foundry agent identity gains KV `secrets: set` so the Hub can persist connection secrets);
- an **AML private endpoint** (`groupId: amlworkspace`) resolving the AML private DNS zones
  `privatelink.api.azureml.ms` + `privatelink.notebooks.azure.net`;
- **managed-network isolation** (`managedNetwork.isolationMode: AllowInternetOutbound`);
- data-plane roles for the Hub identity on its own storage (Storage Blob Data Contributor + Storage
  File Data Privileged Contributor).

The AOAI account + both deployments remain live independently — the app calls AOAI directly over its
private endpoint and does not depend on the managed runtime to function.

---

## Post-provision steps (separate build tasks)

1. Populate Key Vault secrets — full Postgres DSNs for `agents_home_writer` / `provider_home_writer`,
   plus the BCS idempotency key.
2. Run Postgres migrations (`governance/agents_home_schema.sql` + `provider_home_schema.sql` via
   `migrators/postgres_migrator.py`) once the DSNs exist.
3. Seed / verify the Cosmos containers from the application.
4. Update GitHub and push the application code to Azure after CI.

---

## Acceptance checks

`doctors/acceptance_doctors.py` (D01–D12, runnable via `run_doctors.sh`) proves the
no-authority-inversion invariants against the real stack — Postgres governance ledger,
Cosmos agent memory, and the Fabric / OneLake corpus. Run `--static-only` for artifact
analysis, or set `BCS_GATE_URL` + `PG_AGENTS_HOME_DSN` for the live D12 end-to-end claim check.

---

## File map

```
infra/mydude/
  manifest.yaml                    # Identity-first provisioning manifest (Cosmos + Fabric authority model)
  PROVISIONING.md                  # This file
  bicep/
    main.bicep                     # Entrypoint — wires all modules; fabricEnabled / foundryHubEnabled gates
    parameters.json                # Non-secret params (fabricEnabled:false, foundryHubEnabled:false, ...)
    modules/
      identity.bicep               # 5 user-assigned managed identities
      network.bicep                # VNet, subnets, private endpoints, DNS, deny-public policy
      keyvault.bicep               # Key Vault + access policies + PE
      postgres.bicep               # PostgreSQL Flexible Server (agents_home + provider_home), SameZone HA
      storage.bicep                # ADLS Gen2 (knowledge-raw, onelake-staging, lancedb-l2, mlflow-artifacts, offline-sync)
      cosmos.bicep                 # Cosmos DB NoSQL+vector (agents_memory: episodic, vectors, documents)
      fabric.bicep                 # Microsoft Fabric capacity (gated)
      foundry.bicep                # AOAI account + fg/bg deployments; Hub/Project + dedicated NON-HNS storage, AML PE/DNS, managed-net (gated)
      monitoring.bicep             # Log Analytics + App Insights + provider-latency alert
  local/
    deploy.py                      # Azure Python SDK deploy driver (validate/whatif/deploy/status)
    sovereign_stack.yaml           # Local sovereign stack definition
  governance/                      # agents_home / provider_home DDL + migration lineage (governance.claim_ledger)
  migrators/                       # postgres_migrator.py (DDL) + corpus_migrator.py (Fabric/OneLake corpus)
  gates/                           # BCS gate (sole governance-ledger writer) + model promotion gate
  routing/                         # jurisdiction ladder + offline route table
  doctors/                         # D01-D12 acceptance checks (no-authority-inversion proofs)
```
