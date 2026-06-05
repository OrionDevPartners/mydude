# Memory Index

- [Testing auth-gated routes](testing-auth.md) — ADMIN_PASSWORD secret blocks runTest browser login; use in-process FastAPI TestClient with a minted session cookie.
- [Vault env-var lifecycle](vault-env-sync.md) — secrets pushed to os.environ must be cleared on disable AND delete; delete must capture env var before the row is gone.
- [Runtime connector status](connector-runtime-status.md) — read live Replit integration status from the connector proxy at app runtime via REPL_IDENTITY.
- [Static asset caching](static-asset-caching.md) — Replit preview serves stale /static CSS/JS; fix with no-store header + ?v= query, not specificity hacks.
- [Provider-agnostic architecture](provider-agnostic-architecture.md) — three-layer code→env_1(config/providers.toml)→env_2(secrets); boot handshake fail-fasts only on llm.required so the vault app still boots empty.
