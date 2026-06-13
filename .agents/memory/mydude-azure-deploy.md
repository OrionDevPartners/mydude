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
**How to apply:** gate it behind a `fabricEnabled` param (default true, false for SP deploys); a
tenant/Fabric admin (or AAD-authorized SP) creates it; OneLake workspace+lakehouse items are SaaS
(portal/API, not ARM).

## AI Foundry Hub (MachineLearningServices/workspaces kind='Hub') needs real backing resources
Creating a Hub with no `storageAccount`/`keyVault` fails with the opaque
`InternalServerError: Received 400`. A Hub requires a dedicated **GPv2 NON-HNS** storage account
(an HNS-enabled ADLS account is NOT usable as AML primary storage), plus Key Vault, App Insights,
and (for private) an AML private endpoint + AML private DNS (`privatelink.api.azureml.ms`,
`privatelink.notebooks.azure.net`) and managed-network isolation.
**Decision:** gate the Hub/Project behind `foundryHubEnabled` and ship the AOAI account+deployments
alone (the app calls AOAI directly over its PE; the managed runtime is not required to function).

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
