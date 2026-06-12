---
name: Vault->env key sync must not delete env/connector secrets
description: Why syncing the API-key vault into environment variables must never clobber secrets sourced from env or a connector.
---

When the credential vault syncs its keys into process env vars for providers, it must
only manage the vars the vault OWNS. A provider's WORKING credential often comes from an
env secret or the connector proxy (e.g. a Google AI Studio OAuth token) that has no vault
row. A naive sync that deletes env vars for any provider lacking a vault entry wipes the
live credential and silently breaks that provider.

**Why:** the credential resolved at runtime can beat a stale/broken vault or env key;
deleting "unmanaged" env vars during sync removed the only working provider credential and
the optimizer/swarm then reported no provider available.
**How to apply:** sync should add/update vault-owned vars and leave foreign
env/connector-sourced secrets untouched. Never delete an env var just because the vault
has no matching entry.
