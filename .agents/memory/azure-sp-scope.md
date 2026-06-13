---
name: Azure SP scope for MyDude deploy
description: The MyDude Azure service principal is resource-group-scoped, which changes how the Bicep deploy must run.
---

# Azure SP is RG-scoped, not subscription-scoped

The Azure service principal provided for the MyDude deploy is **Owner on the
resource group `mydude` (lowercase) only — NOT the subscription** (as provided
2026-06-12; re-verify scope before deploying in case it was re-created wider).

**Why:** the SP role assignment scope is the RG, so any operation that needs
subscription-level rights will be denied.

**How to apply when running the live deploy:**
- Do **not** run `az group create` — creating/managing a resource group needs
  subscription-level permission the SP lacks. Target the **existing** `mydude` RG.
- Use the exact RG name casing **`mydude`** (lowercase), not `MyDude`.
- `az deployment group create` (scoped to the RG) is the right command; avoid
  `az deployment sub create`.
- The client secret was shared in plaintext once; user agreed to rotate it after
  deploy. Never echo or commit it.

## Resource-provider registration is a subscription-scoped gate the SP CANNOT pass

`*/register/action` is a **subscription-level** action. The RG-Owner SP gets
`AuthorizationFailed` trying to register any provider. Confirmed NotRegistered and
un-registerable by the SP: **Microsoft.DocumentDB** (Cosmos), **Microsoft.Fabric**,
**Microsoft.KeyVault**, **Microsoft.OperationalInsights**, **Microsoft.Insights**.
(Already registered: CognitiveServices, DBforPostgreSQL, Network, Storage,
ManagedIdentity.)

**Why:** registering a provider mutates subscription state; RG-scoped Owner is not enough.

**How to apply:** before any live deploy that uses an unregistered provider, the
**subscription admin** (the user, via Azure Portal → Subscription → Resource
providers → Register, or `az provider register -n <ns>` with a subscription-scoped
login) must register them once. ARM `validate`/`what-if` and the deploy will fail
with "subscription is not registered to use namespace …" until then. Registration
is free, one-time, ~1-2 min each, and does not bill.

## Other deploy gates discovered

- **Fabric capacity needs ≥1 admin member** (`properties.administration.members`,
  UPNs/emails or AAD object IDs). No sane default — must be supplied at deploy.
- **Fabric/OneLake private-link** is tenant/admin-scoped, NOT expressible in the
  RG-scoped template → post-deploy admin step.
- **bicep CLI is not persistent** in this container (the env reset wipes
  `~/.azure/bin/bicep`); reinstall with `az bicep install` (old az at
  /nix/store/*azure-cli-2.44.1*/bin/az). The bicep binary itself needs
  `DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1` (no ICU in container).
