---
name: Vault env-var lifecycle
description: Constraint for the API vault that pushes stored secrets into os.environ for the LLM providers.
---

`sync_keys_to_env()` decrypts active vault keys and writes them to `os.environ` under each key's env var so the LLM swarm can use them.

**Rule:** A secret must never linger in `os.environ` after its key is disabled or deleted.
- Compute the set of env vars to clear from **all** keys (active + inactive) plus the legacy map, then clear them, then re-set only active keys.
- On **delete**, the row is gone before `sync` runs, so the delete route must capture `_resolve_env_var(key)` and `os.environ.pop` it *before* deleting; `sync` then re-adds it from any remaining active key sharing the same env var.

**Why:** A first implementation built the clear-set from active keys only, so a disabled/deleted custom-`env_var` secret stayed usable in-process (flagged in code review). Multiple active keys can target the same env var → last-write-wins (accepted, matches original behavior).
