# Memory Index

- [Testing auth-gated routes](testing-auth.md) — ADMIN_PASSWORD secret blocks runTest browser login; use in-process FastAPI TestClient with a minted session cookie.
- [Vault env-var lifecycle](vault-env-sync.md) — secrets pushed to os.environ must be cleared on disable AND delete; delete must capture env var before the row is gone.
- [Runtime connector status](connector-runtime-status.md) — read live Replit integration status from the connector proxy at app runtime via REPL_IDENTITY.
