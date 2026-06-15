---
name: MyDude Azure stack deploy gotchas
description: Non-obvious Azure constraints/races hit deploying the MyDude Bicep stack (RG-Owner SP, eastus2, private) — and the decisions taken.
---

# MyDude Azure deploy — durable constraints & decisions

Stack deploys into the EXISTING RG `mydude` (lowercase, eastus2) via `infra/mydude/local/deploy.py`
(Azure Python SDK, Incremental). The service principal is **Owner on RG `mydude` only** — it cannot
do any subscription/tenant-scoped op (no RG create, no provider registration, no subscription-scoped
role/policy, no support tickets). That scope shapes every workaround below.

## Postgres HA: ZoneRedundant blocked in eastus2 offer → SameZone
ZoneRedundant (Multi-Zone) HA fails with `MultiAzHaIsOfferRestricted` in eastus2 on this
subscription's offer. Use `highAvailability.mode: 'SameZone'` (no `standbyAvailabilityZone`).
**Why:** the region/offer disallows multi-zone HA; SameZone (hot standby in the same AZ) is allowed.
**How to apply:** upgrading to ZoneRedundant in place is a support-ticket / subscription-admin step,
out of scope for the RG-Owner SP — leave SameZone and note it.

## Microsoft.Fabric/capacities can't be created by an RG-scoped SP
Fails with "Unable to authorize with Azure Active Directory" — capacity creation needs AAD/Graph
authorization an RG-scoped SP cannot satisfy, even with a human admin UPN in `fabricAdminMembers`.
**How to apply:** gate it behind a `fabricEnabled` param (Bicep default true; **keep it false in
`parameters.json`, which is the SP deploy's param file** — flipping it true there fails every SP
deploy at the Fabric module). A tenant/Fabric admin (or AAD-authorized SP) creates it; OneLake
workspace+lakehouse items are SaaS (portal/API, not ARM).

## AI Foundry Hub (MachineLearningServices/workspaces kind='Hub') needs real backing resources
Creating a Hub with no `storageAccount`/`keyVault` fails with the opaque
`InternalServerError: Received 400`. A Hub requires a dedicated **GPv2 NON-HNS** storage account
(an HNS-enabled ADLS account is NOT usable as AML primary storage), plus Key Vault, App Insights,
and (for private) an AML private endpoint (`groupId: amlworkspace`) + AML private DNS
(`privatelink.api.azureml.ms`, `privatelink.notebooks.azure.net`) and managed-network isolation.
**Now wired & enabled (`foundryHubEnabled=true`):** `foundry.bicep` provisions a dedicated
`mydudefoundrystg` (StorageV2, isHnsEnabled:false) with blob+file PEs/DNS, wires the shared
`mydude-kv` + `mydude-appinsights` by ID (foundry identity needs KV `secrets:set`), adds the
`amlworkspace` PE, and sets `managedNetwork.isolationMode: AllowInternetOutbound`. A user-assigned-
identity Hub also needs Storage Blob Data Contributor + Storage File Data Privileged Contributor on
its OWN storage (AML doesn't auto-grant). All Hub-side resources gate on `foundryHubEnabled`.
**Why:** the AOAI account+deployments ship/work independently over their PE; the Hub is the managed
agent runtime layer added on top.

## AOAI account+deployments race on Incremental re-runs ("account in state Accepted")
`AccountProvisioningStateInvalid: Account ... in state Accepted` appears intermittently on redeploys.
**Why:** re-PUTting account-adjacent resources (the AOAI private endpoint, DNS zone group, role
assignment) flips the CognitiveServices account into a transient `Accepted` state, and a model
deployment create that runs concurrently is rejected; also AOAI serializes deployment ops per account.
**How to apply:** make the FOREGROUND deployment `dependsOn` the account's PE + DNS group + role
assignment, and the BACKGROUND deployment `dependsOn` the foreground one. Fully serialized = no race.

## Cosmos vector container needs dedicated throughput
A diskANN vector index is rejected on a shared database-level throughput offer
("Vector Indexing is not supported for shared throughput offer"). Give the `vectors` container its
own `options.autoscaleSettings.maxThroughput`; the other containers can stay on the shared DB offer.

## MCP Container App public posture (opt-in) is immutable + must be host-pinned from phase 1
The MCP dev accelerator (`mcp.bicep`) defaults to VNet-internal (governance default). Opting into a
PUBLIC custom apex domain (bearer token = sole gate) has two hard constraints:
- **`managedEnvironments.vnetConfiguration.internal` is IMMUTABLE post-create.** Flipping posture on
  an existing env requires DELETE+RECREATE of the (stateless) MCP env+app — KV holds the secrets, so
  it's safe. A managed cert for an apex domain needs a two-phase deploy: phase 1 (public ingress,
  empty `customDomain`) to read `managedEnvStaticIp` + `customDomainVerificationId` → create apex
  A-record→staticIp and TXT `asuid.<domain>`→verificationId → phase 2 set `customDomain` to mint+bind
  the cert (`SniEnabled`).
- **Public ingress MUST be host-pinned from the FIRST public deploy, never just at bind time.** An
  empty allow-list + public ingress would otherwise silently disable DNS-rebinding protection and
  expose the default FQDN. Defense is layered: Bicep only opts the host check out when
  `empty(allowedHosts) && !externalIngress` (public mode never opts out → fails CLOSED to SDK
  localhost-only), the deploy preflight (`deploy.py validate_mcp_posture`) FAILS LOUD before any
  billable deploy if public has no pin / a custom domain isn't external+in-allowlist, and the doctor
  (`--public-domain`) asserts it post-deploy. Pinning to the not-yet-resolving domain in phase 1 is
  correct — it keeps the public default FQDN rejecting all Host headers until DNS points at it.
**Why:** posture is a deliberate, irreversible-ish governance change; the gap (public + empty pin)
is the one way the sole-gate token guarantee gets silently bypassed.
