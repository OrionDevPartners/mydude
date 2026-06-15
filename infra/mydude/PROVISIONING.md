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

### Azure MCP Dev Accelerator (`deployAzureMcp=false` — GATED, off by default)

A governed **MCP server** (`src/mcp/azure_dev_server.py`, FastMCP streamable HTTP) deployed as a
**VNet-internal Azure Container App** so an MCP client *inside the VNet* (jump box, dev container,
Foundry agent) can drive the private `mydude` stack through governed tools. Every tool is dispatched
through the existing contract→policy→broker→integration→audit chain — there is **no raw provider
passthrough**:

- `azure_cosmos_read` — read items from `agents_memory` containers (read-only clamps).
- `azure_pg_select` — **SELECT/WITH-only** queries against `agents_home` / `provider_home` (a strict
  single-statement validator rejects DML/DDL/multi-statement/`;`-injection).
- `azure_deploy_status` — ARM deployment + resource state for `mydude-stack`.
- `azure_aoai_complete` — a **governed** completion routed through `src.swarm.service.run_governed_swarm`
  (full compliance / hallucination / provenance / audit envelope) — never raw AOAI output.
- `azure_deploy_plan` — Bicep **what-if** (no cost) returning a short-lived signed plan token.
- `azure_deploy_apply` — **BILLABLE, destructive, default-deny.** Requires `ALLOW_AZURE_DEPLOY=true`
  **and** the matching plan token + exact plan/params hash + explicit confirm (broker-gated, both
  phases audited). Off unless `azureMcpEnableDeploy=true`.

**Security posture (default):** ingress `external:false` (private FQDN only) — this is the
**governance-default posture** and stays the default. Bearer-token auth is sourced from Key
Vault at runtime under the `mydude-foundry-agent` user-assigned identity (the token value is never
baked into the image or Bicep), `allowInsecure:false`. The image bakes the Bicep CLI so the
plan/apply tools are genuinely functional (no placeholders) when enabled. To expose the server on a
**public custom domain** instead, see *Public custom domain (opt-in)* below — there the bearer token
becomes the **sole** gate, so host-pinning is hard-required.

The two-phase deploy tools sign/verify their short-lived plan tokens with a **stable** signing secret,
**also** sourced from Key Vault at runtime (`AZURE_MCP_DEPLOY_SECRET_NAME`, default
`azure-mcp-deploy-token-secret`) under the same identity — a stable secret is required so a token minted
by `azure_deploy_plan` still verifies in `azure_deploy_apply` across container restarts/replicas (an
ephemeral per-process key would silently break the plan→apply binding). This secret is **server-only**:
clients never need it. The destructive apply additionally **refuses to run** unless a durable audit
record can first be written (governance pillar #4).

> **Subscription-admin prerequisite:** the **`Microsoft.App`** and **`Microsoft.OperationalInsights`**
> resource providers must be **Registered** on the subscription before this module deploys (the
> RG-Owner SP cannot register providers). Register once:
> `az provider register --namespace Microsoft.App` and
> `az provider register --namespace Microsoft.OperationalInsights`.

**Connection guide (deploy → wire → connect):**

1. **Build & push the image** (build context is the repo **root**), to a registry the
   `mydude-foundry-agent` identity can pull from (e.g. an ACR with AcrPull granted to that identity):
   ```bash
   docker build -f infra/mydude/mcp/Dockerfile -t <registry>/mydude-azure-mcp:<tag> .
   docker push <registry>/mydude-azure-mcp:<tag>
   ```
2. **Deploy the module** by flipping the gate (admin/authorized run; `parameters.json` keeps it false):
   ```bash
   # via the Python driver, overriding the gated params for this run:
   python3 infra/mydude/local/deploy.py deploy --yes \
     # set in parameters.json or pass through: deployAzureMcp=true,
     # azureMcpImage=<registry>/mydude-azure-mcp:<tag>, azureMcpRegistryServer=<registry>
   ```
   (Leave `azureMcpEnableDeploy=false` unless you intend to expose the billable deploy-apply tool.)
3. **Mint the MCP secrets into Key Vault** (run from inside the VNet, identity with KV `set`; values
   are never printed). One call mints BOTH the client **bearer token** (`azure-mcp-auth-token`) and the
   server-only deploy-token **signing secret** (`azure-mcp-deploy-token-secret`):
   ```bash
   python3 infra/mydude/local/setup_mcp_token.py --dry-run   # preview
   python3 infra/mydude/local/setup_mcp_token.py             # create either if absent (or --rotate)
   ```
4. **Verify the deployment** (control-plane reads only; never prints the token):
   ```bash
   python3 infra/mydude/local/azure_mcp_doctor.py
   ```
5. **Connect an MCP client** from inside the VNet. The endpoint is the internal app FQDN
   (`azureMcpUrl` output) at path **`/mcp`**; authenticate with the bearer token you retrieve with
   **your own** credentials:
   ```bash
   TOKEN="$(az keyvault secret show --vault-name mydude-kv --name azure-mcp-auth-token --query value -o tsv)"
   # Point your MCP client at https://<azureMcpUrl> with header: Authorization: Bearer $TOKEN
   ```
   To smoke-test interactively, run the MCP Inspector against that URL with the same bearer header.

#### Public custom domain (opt-in)

The default posture is VNet-internal (above) and **stays the default**. Exposing the server on a
public apex domain (e.g. `MydudeMCP.com`) is an explicit, deliberate posture change: the endpoint
becomes internet-reachable and the **bearer token is then the sole gate**. Only do this when you
accept that trade-off; the governance pillars still apply (no placeholders, secrets via Key Vault,
governed inference, TLS always on).

**Prerequisites & invariants:**
- You must **own the domain** and be able to create DNS records at its registrar/zone.
- `azureMcpExternalIngress` flips `managedEnvironments.vnetConfiguration.internal`, which is
  **immutable after the environment is created**. Switching an existing internal env to public (or
  back) therefore requires **deleting and recreating** the MCP environment + app. They are
  **stateless** (no data lives in them — Key Vault holds the secrets), so a delete/recreate is safe.
- Host-pinning becomes **hard-required** in public mode: `azureMcpAllowedHosts` must include the
  custom domain and the host-check opt-out must be off. TLS is always enforced (`allowInsecure:false`)
  via a free, auto-renewed **Azure-managed certificate**.

**Two-phase flow (the chicken-and-egg of DNS + managed cert):**

1. **Phase 1 — provision public ingress, get the DNS targets.** Deploy with public ingress but
   **no** custom domain yet (Azure can't mint a cert for a domain whose DNS records don't exist).
   Public ingress is the sole-gate case, so the host pin is **required from the very first public
   deploy** — set `azureMcpAllowedHosts=MydudeMCP.com` now even though the domain isn't bound yet.
   This makes the public default FQDN reject every Host header except the intended domain (which does
   not resolve to it yet), so the endpoint stays effectively closed during phase 1. The deploy
   preflight (`validate_mcp_posture`) **fails loud** if you try public ingress without it, and the
   Bicep never opts the host check out in public mode:
   ```bash
   python3 infra/mydude/local/deploy.py deploy --yes \
     # deployAzureMcp=true, azureMcpExternalIngress=true, \
     # azureMcpCustomDomain='' (empty), azureMcpAllowedHosts=MydudeMCP.com
   ```
   Read the two DNS targets from the stack outputs:
   - `azureMcpStaticIp` — the managed environment's static inbound IP.
   - `azureMcpCustomDomainVerificationId` — the domain-ownership verification id.
2. **Create the DNS records** at your registrar and wait for propagation:
   - Apex **A**-record: `MydudeMCP.com` → `azureMcpStaticIp`.
   - **TXT** record: `asuid.MydudeMCP.com` → `azureMcpCustomDomainVerificationId`.

   **Cloudflare DNS (this domain):** Cloudflare defaults new A/AAAA/CNAME records to **Proxied
   (orange cloud)**, which terminates TLS at Cloudflare's edge and hides the Azure origin — that
   **breaks** Azure managed-certificate domain-control validation, binding, AND the silent
   auto-renewal. For the managed-cert flow below, set the apex **A**-record to **DNS-only (grey
   cloud)** so the domain resolves straight to Azure's static IP. The `asuid` **TXT** record is never
   proxied, so leave it as-is. Cloudflare supports A-records at the apex natively (no CNAME-flattening
   needed). Keep the A-record DNS-only permanently unless you switch to the proxied posture below.

   _Optional — keep Cloudflare's proxy/WAF/CDN in front:_ leave the A-record **Proxied**, set
   Cloudflare **SSL/TLS → Full (strict)**, and bind the Azure managed cert with the A-record
   temporarily set to DNS-only first (so issuance succeeds), then flip it back to Proxied — or point
   Cloudflare's origin at the Container App's default `*.azurecontainerapps.io` FQDN, which already
   serves a trusted cert. Either way Cloudflare preserves the `Host: MydudeMCP.com` header, so the
   MCP host pin (`azureMcpAllowedHosts=MydudeMCP.com`) and the doctor's `--public-domain` check are
   unaffected. Note: Azure managed-cert auto-renewal re-validates against the origin, so the proxied
   posture needs the origin reachable at renewal time — DNS-only is the lower-maintenance choice.
3. **Phase 2 — bind the domain + mint the certificate + pin the host.** Re-deploy with the domain set
   and the host allow-list pinned to it:
   ```bash
   python3 infra/mydude/local/deploy.py deploy --yes \
     # deployAzureMcp=true, azureMcpExternalIngress=true, \
     # azureMcpCustomDomain=MydudeMCP.com, azureMcpAllowedHosts=MydudeMCP.com
   ```
   This deploy creates the managed certificate (`domainControlValidation` defaults to `TXT`; use
   `CNAME`/`HTTP` for a subdomain) and binds it to the ingress as `SniEnabled`.
4. **Verify the public posture** (asserts external env + public ingress + domain bound to a cert +
   host-pinned; fails loud otherwise):
   ```bash
   python3 infra/mydude/local/azure_mcp_doctor.py --public-domain MydudeMCP.com
   ```
5. **Connect** any MCP client (now from anywhere) to `https://MydudeMCP.com/mcp` (the `azureMcpUrl`
   output resolves to this once the domain is bound) with the same `Authorization: Bearer <token>`
   header. Mint/rotate the token exactly as in the internal flow (`setup_mcp_token.py`).

**Public-posture parameters:**

| Parameter | Default | Public-posture value |
| --- | --- | --- |
| `azureMcpExternalIngress` | `false` (internal) | `true` |
| `azureMcpCustomDomain` | `''` | `MydudeMCP.com` (phase 2) |
| `azureMcpDomainValidation` | `TXT` | `TXT` (apex) / `CNAME` / `HTTP` |
| `azureMcpAllowedHosts` | `''` | `MydudeMCP.com` (**required** in public mode) |

---

## Post-provision steps (separate build tasks)

> **Run location:** all four services are private (public network access disabled + private
> endpoints), so these steps must run from **inside the `mydude` VNet** — a jump box or a
> VNet-integrated container — NOT from a workspace outside Azure. The scripts use
> `DefaultAzureCredential`, so they pick up a user-assigned managed identity on the VNet host
> (preferred) or the `AZURE_*` service-principal env. The identity needs: Key Vault get/set
> (e.g. `mydude-bcs-gate`), Cosmos "Built-in Data Contributor" (`mydude-agents-home-db`), and
> Cognitive Services OpenAI User (`mydude-foundry-agent`). All scripts are fail-loud and idempotent.

1. **Populate Key Vault secrets** — full Postgres DSNs (`agents-home-pg-dsn`,
   `provider-home-pg-dsn`) + the BCS idempotency key (`bcs-idempotency-key`):
   ```bash
   # writer-role passwords are read from the env, never hardcoded:
   export PG_AGENTS_HOME_WRITER_PASSWORD=... PG_PROVIDER_HOME_WRITER_PASSWORD=...
   python3 infra/mydude/local/populate_keyvault.py --dry-run   # preview (no writes)
   python3 infra/mydude/local/populate_keyvault.py             # set the 3 secrets
   ```
2. **Run Postgres migrations** (`governance/*_schema.sql` via `migrators/postgres_migrator.py`).
   Pass `--from-keyvault` to source the DSNs + `BCS_LEASE_SECRET` from the vault (pillar #3);
   set the reader/writer role-password envs so the role credential bootstrap can run:
   ```bash
   export PG_ADMIN_PASSWORD=... \
          PG_AGENTS_HOME_WRITER_PASSWORD=... PG_AGENTS_HOME_READER_PASSWORD=... \
          PG_PROVIDER_HOME_WRITER_PASSWORD=... PG_PROVIDER_HOME_READER_PASSWORD=...
   python3 infra/mydude/migrators/postgres_migrator.py --from-keyvault --dry-run
   python3 infra/mydude/migrators/postgres_migrator.py --from-keyvault
   ```
3. **Seed / verify the Cosmos containers** from the app (`agents_memory`: `episodic`, `vectors`,
   `documents` — created by `cosmos.bicep`; the script verifies + probe-writes, never creates):
   ```bash
   python3 infra/mydude/local/seed_cosmos.py --verify-only   # read-only
   python3 infra/mydude/local/seed_cosmos.py                 # verify + probe write/read
   ```
4. **Verify end-to-end reachability** (AOAI + Cosmos + Postgres over the private endpoints):
   ```bash
   python3 infra/mydude/local/dataplane_doctor.py --no-spend  # skip the billed AOAI call
   python3 infra/mydude/local/dataplane_doctor.py             # full check
   ```
5. Update GitHub and push the application code to Azure after CI.

Shared wiring helper: `infra/mydude/local/azure_common.py` (credential, ARM-output resolution,
Key Vault get/set, DSN builder, `hydrate_env_from_keyvault()`).

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
      mcp.bicep                    # Azure MCP Dev Accelerator — managedEnvironment + containerApp (gated: deployAzureMcp); VNet-internal by default, opt-in public custom domain + managed cert
  mcp/
    Dockerfile                     # Governed MCP server image (build context = repo ROOT; bakes bicep CLI)
    entrypoint.sh                  # Fail-loud preflight (AZURE_SUBSCRIPTION_ID + auth source); never prints secrets
  local/
    deploy.py                      # Azure Python SDK deploy driver (validate/whatif/deploy/status)
    setup_mcp_token.py             # Mint the MCP bearer token into Key Vault (value never printed; --rotate/--dry-run)
    azure_mcp_doctor.py            # Validate the deployed MCP app is wired + token present (control-plane reads only); default=internal, --public-domain <d> asserts public posture
    sovereign_stack.yaml           # Local sovereign stack definition
  governance/                      # agents_home / provider_home DDL + migration lineage (governance.claim_ledger)
  migrators/                       # postgres_migrator.py (DDL) + corpus_migrator.py (Fabric/OneLake corpus)
  gates/                           # BCS gate (sole governance-ledger writer) + model promotion gate
  routing/                         # jurisdiction ladder + offline route table
  doctors/                         # D01-D12 acceptance checks (no-authority-inversion proofs)
```
