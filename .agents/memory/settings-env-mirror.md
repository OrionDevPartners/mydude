---
name: Non-secret settings → env mirror
description: How dashboard-configurable (non-secret) settings reach the running swarm with no restart, and how to test it.
---

# Non-secret settings mirrored to os.environ

Operator-editable, **non-secret** config (e.g. local-node base URLs, probe
timeouts) is persisted in the `app_settings` table via `src/web/settings_store.py`
and **mirrored into `os.environ`** on write (`set_setting`/`delete_setting`) and on
boot (`sync_settings_to_env`, wired in app.py startup). The swarm reads config
through its normal env path (adapters' `_base_url()`, `get_env`, jurisdiction probe
timeouts), so a dashboard change takes effect immediately — **no restart**.

**Why:** keeps one resolution path (env) for dev/prod and avoids train/serve skew
between the dashboard view, the live probe, and actual inference. Secrets stay in
the vault; only non-secret settings live here.

**How to apply:**
- New dashboard-configurable non-secret setting → add its key to the env-managed
  set so it gets mirrored; derive keys from `config/providers.toml` rather than
  hardcoding (provider-agnostic pillar).
- Empty value = clear the override (`delete_setting`) → reverts to the code default.
- Testing the store offline: `settings_store` binds `SessionLocal` into its **own
  module namespace** at import, so patch `src.web.settings_store.SessionLocal`
  (NOT `src.database.SessionLocal`) to redirect at a throwaway SQLite DB.
