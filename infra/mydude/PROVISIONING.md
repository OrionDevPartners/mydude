# MyDude Total Stack Provisioning Guide

## What this is

The MyDude sovereign + Azure capacity stack — identical pattern to the Bonado TOTAL STACK
but as a fully **independent** deployment. MyDude gets its own resource group (`MyDude`),
own identities, own catalog, own Postgres, and own local sovereign stack.

**Bonado is completely out of scope.** No shared resource groups, identities, catalog, storage,
or Postgres. MyDude is a parallel, isolated deployment.

## Architectural Rules (locked)

1. **MyDude owns routing authority** — `agents_home` is the only governance authority.
2. **The BCS promotion gate is the only truth writer** — single Entra managed identity,
   idempotency keys, lease lock, V1-V7 scope gates.
3. **Every Azure / agent-runtime service is an ADAPTER or PROJECTION, never an authority** —
   agent-runtime auto-memory and outside-the-agent policy are replaced, not adopted.
4. **exec_locus must match domain pin** — a model on the wrong infrastructure can never
   satisfy an `exec_locus`-pinned domain.

---

## What credentials are needed for live provisioning

To complete live Azure provisioning, you need to provide these secrets via the Replit
Secrets vault (never hardcoded):

| Secret name | Description |
|-------------|-------------|
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID to deploy into |
| `AZURE_TENANT_ID` | Azure AD tenant ID |
| `AZURE_CLIENT_ID` | Service principal or managed identity client ID with Contributor on the subscription |
| `AZURE_CLIENT_SECRET` | Service principal secret (or use `AZURE_USE_MSI=true` for managed identity) |
| `PG_ADMIN_PASSWORD` | PostgreSQL admin password for initial setup (Flexible Server admin, stored in KV afterward) |
| `PG_AGENTS_HOME_DSN` | Full DSN for agents_home admin connection (`postgresql://admin:pass@host/agents_home`) |
| `PG_PROVIDER_HOME_DSN` | Full DSN for provider_home admin connection (`postgresql://admin:pass@host/provider_home`) |
| `PG_AGENTS_HOME_WRITER_PASSWORD` | Password to set for the `agents_home_writer` role (used by BCS gate DSN) |
| `PG_AGENTS_HOME_READER_PASSWORD` | Password to set for the `agents_home_reader` role (used by read-only projections) |
| `PG_PROVIDER_HOME_WRITER_PASSWORD` | Password to set for the `provider_home_writer` role (used by BCS outbox) |
| `PG_PROVIDER_HOME_READER_PASSWORD` | Password to set for the `provider_home_reader` role (used by outbox replay reader) |

> **Credential bootstrap flow** (`postgres_migrator.py`):
> 1. Set all `PG_*_PASSWORD` env vars in Replit Secrets before running the migrator.
> 2. `postgres_migrator.py --db all` applies DDL (which creates the roles with `LOGIN`) then calls
>    `ALTER ROLE <role> PASSWORD '<env_var>'` for each role — idempotent, safe to re-run.
> 3. After bootstrap, build and store the application DSNs (e.g.
>    `postgresql://agents_home_writer:<password>@<host>/agents_home`) in Key Vault as
>    `agents-home-pg-dsn` and `provider-home-pg-dsn`.  These KV secrets are referenced by
>    `parameters.kv.json` on all subsequent deploys.

Minimum RBAC on the subscription (or `MyDude` resource group if pre-created):
- **Contributor** — to create all resources
- **Role Based Access Control Administrator** — to assign managed identity roles (Storage, Search)
- **Key Vault Administrator** (or Contributor) — to create Key Vault secrets

---

## Provisioning steps

### 1. Authenticate to Azure

```bash
az login
az account set --subscription <AZURE_SUBSCRIPTION_ID>
```

Or using a service principal:
```bash
az login --service-principal -u <client_id> -p <client_secret> --tenant <tenant_id>
```

### 2. Create the resource group

```bash
az group create --name MyDude --location eastus2
```

### 3. Deploy the Bicep IaC (staged — two passes required)

Deployment is split into **two passes** because Key Vault must exist before
`parameters.kv.json` can resolve its secret references. On first deploy, supply
the Postgres admin password directly; on subsequent deploys, use the KV reference.

**Pass 1 — first-time deployment (plain password):**
```bash
cd infra/mydude/bicep

# Edit parameters.json: set TENANT_ID and replace REPLACE_ME_BEFORE_DEPLOY with your password
az deployment group create \
    --resource-group MyDude \
    --template-file main.bicep \
    --parameters @parameters.json
```

**Pass 2 — store the password in Key Vault, then re-deploy using KV ref:**
```bash
KV=mydude-kv
az keyvault secret set --vault-name $KV --name pg-admin-password --value "<PG_ADMIN_PASSWORD>"

# Edit parameters.kv.json: set SUBSCRIPTION_ID and TENANT_ID, then:
az deployment group create \
    --resource-group MyDude \
    --template-file main.bicep \
    --parameters @parameters.kv.json
```

> **Parameters files:**
> - `parameters.json` — plain-value template for first-time deploy (bootstrapping)
> - `parameters.kv.json` — Key Vault reference template for all subsequent deploys

**Optional capability gates (browser/voice — disabled by default):**
```bash
# Enable browser capability (Azure Playwright Service):
az deployment group create ... --parameters foundryBrowserEnabled=true

# Enable voice capability (Azure Communication Services + Speech):
az deployment group create ... --parameters foundryVoiceEnabled=true
```

### 4. Set Key Vault secrets

After provisioning, populate the Key Vault with runtime secrets.

**IMPORTANT: the BCS gate needs full DSN connection strings, not bare passwords.**
The Postgres advisory lock (cross-replica idempotency) requires a live psycopg2 connection,
which needs the complete DSN. Store the full connection string as shown below:

```bash
KV=mydude-kv
PG_HOST="mydude-pg.postgres.database.azure.com"
AH_PW="<agents_home_writer password>"
PH_PW="<provider_home_writer password>"

# Full DSN strings — these are what the BCS gate Container App reads
az keyvault secret set --vault-name $KV \
  --name agents-home-pg-dsn \
  --value "postgresql://agents_home_writer:${AH_PW}@${PG_HOST}:5432/agents_home?sslmode=require"

az keyvault secret set --vault-name $KV \
  --name provider-home-pg-dsn \
  --value "postgresql://provider_home_writer:${PH_PW}@${PG_HOST}:5432/provider_home?sslmode=require"

# Idempotency signing key (random 32-byte hex)
az keyvault secret set --vault-name $KV \
  --name bcs-gate-idempotency-key \
  --value "$(openssl rand -hex 32)"
```

Secret names referenced by container_apps.bicep:
| Key Vault secret name        | Referenced by                  | Contains                                |
|------------------------------|--------------------------------|-----------------------------------------|
| `agents-home-pg-dsn`         | BCS gate → PG_AGENTS_HOME_DSN | Full PostgreSQL DSN for agents_home     |
| `provider-home-pg-dsn`       | BCS gate → PG_PROVIDER_HOME_DSN | Full PostgreSQL DSN for provider_home |
| `bcs-gate-idempotency-key`   | BCS gate → BCS_LEASE_SECRET   | 32-byte hex signing key (V6 gate)       |

### 5. Run Postgres migrations

```bash
export PG_AGENTS_HOME_DSN="postgresql://agents_home_writer:<pw>@mydude-pg.postgres.database.azure.com:5432/agents_home"
export PG_PROVIDER_HOME_DSN="postgresql://provider_home_writer:<pw>@mydude-pg.postgres.database.azure.com:5432/provider_home"
export BCS_LEASE_SECRET="<value from KV>"

cd infra/mydude/migrators
python postgres_migrator.py --db all
```

### 5b. Initialise Unity Catalog metastore (post-Bicep, one-time)

The Databricks workspace is provisioned by Bicep (`unity_catalog.bicep`), but the
Unity Catalog metastore and catalog must be initialised via the Databricks REST API —
this cannot be done in Bicep itself.

```bash
DBR_URL="https://$(az deployment group show \
  --resource-group MyDude \
  --name mydude-unity-catalog \
  --query properties.outputs.databricksWorkspaceUrl.value -o tsv)"

DBR_TOKEN=$(az account get-access-token \
  --resource 2ff814a6-3304-4ab8-85cb-cd0e6f879c1d \
  --query accessToken -o tsv)

# 1. Create the Unity Catalog metastore (once per region)
curl -sX POST "$DBR_URL/api/2.1/unity-catalog/metastores" \
  -H "Authorization: Bearer $DBR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"mydude-metastore","storage_root":"abfss://unity-catalog@mydudesa.dfs.core.windows.net/","region":"eastus2"}'

# 2. Assign the metastore to the workspace
METASTORE_ID=$(curl -s "$DBR_URL/api/2.1/unity-catalog/metastores" \
  -H "Authorization: Bearer $DBR_TOKEN" | python3 -c "import sys,json; m=json.load(sys.stdin)['metastores']; print(m[0]['metastore_id'])")

curl -sX PUT "$DBR_URL/api/2.1/unity-catalog/workspaces/$(az databricks workspace show \
  --resource-group MyDude --name mydude-databricks --query workspaceId -o tsv)/metastore" \
  -H "Authorization: Bearer $DBR_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"metastore_id\":\"$METASTORE_ID\",\"default_catalog_name\":\"mydude\"}"

# 3. Grant BCS gate service principal catalog CREATE privilege
# (Run from within Databricks notebook or via SQL API after metastore assignment)
# GRANT CREATE CATALOG ON METASTORE TO `<bcs-gate-sp-application-id>`;
# CREATE CATALOG IF NOT EXISTS mydude;
```

### 6. Run Unity Catalog migration (via BCS gate)

After the metastore is initialised, submit the DDL through the BCS gate:

```bash
export UNITY_CATALOG_ENDPOINT="$DBR_URL"
export BCS_GATE_URL="https://<bcs-gate-internal-url>"

cd infra/mydude/migrators
python unity_migrator.py --catalog mydude
```

### 7. Run acceptance doctors

```bash
export AZURE_SUBSCRIPTION_ID="<subscription>"
export AZURE_TOKEN=$(az account get-access-token --query accessToken -o tsv)

cd infra/mydude/doctors
./run_doctors.sh
```

All 10 checks (D01-D10) must pass before the stack is considered provisioned.

---

## Local sovereign stack setup

```bash
# Start Ollama
ollama serve &
ollama pull qwen3:14b
ollama pull llama3.2:3b

# Start MinIO
minio server ~/.mydude/minio --address :9000 &

# Start LanceDB L1 (embedded in app)
# agents_home policy cache is populated on first connectivity
```

---

## Verify no-authority-inversion (static, no Azure required)

```bash
cd infra/mydude/doctors
python acceptance_doctors.py --static-only
```

Expected: all 10 checks PASS.

---

## File map

```
infra/mydude/
  manifest.yaml                    # Identity-first provisioning manifest
  PROVISIONING.md                  # This file
  bicep/
    main.bicep                     # Main Bicep entrypoint
    parameters.json                # Parameters (fill SUBSCRIPTION_ID, TENANT_ID)
    modules/
      identity.bicep               # Managed identities (5 roles)
      network.bicep                # VNet, subnets, private endpoints, Azure Policy
      keyvault.bicep               # Key Vault + access policies
      postgres.bicep               # PostgreSQL Flexible Server (agents_home + provider_home)
      storage.bicep                # ADLS Gen2 (Unity Catalog root, LanceDB L2, etc.)
      container_apps.bicep         # BCS Gate (min-1), Master_DB, Fan-out Gateway
      foundry.bicep                # Foundry Agent Service + AOAI
      monitoring.bicep             # Log Analytics + App Insights
      ai_search.bicep              # Azure AI Search (rebuildable projection)
  governance/
    agents_home_schema.sql         # agents_home DDL (routing, policy, governance schemas)
    provider_home_schema.sql       # provider_home DDL (candidates, outbox schemas)
    agents_home_migrations/V001    # agents_home migration lineage (independent)
    provider_home_migrations/V001  # provider_home migration lineage (independent)
  migrators/
    base.py                        # CompletionClaim, ScopeGate V1-V7, submit_completion_claim
    postgres_migrator.py           # Postgres governance migrator (agents_home + provider_home)
    unity_migrator.py              # Unity/Iceberg migrator (via BCS gate)
  gates/
    bcs_gate/
      app.py                       # BCS Promotion Gate (FastAPI Container App)
      Dockerfile                   # Container image
    model_promotion_gate.py        # Model promotion gate with exec-locus assertion
  routing/
    jurisdiction.py                # Nested jurisdiction routing ladder + cloud_shift
    route_table.yaml               # Offline route table (agents_home projection)
  local/
    sovereign_stack.yaml           # Local sovereign stack definition
    local_bcs.py                   # Local BCS path (offline promotion)
  doctors/
    acceptance_doctors.py          # D01-D10 no-authority-inversion acceptance checks
    run_doctors.sh                 # Runner script
```
