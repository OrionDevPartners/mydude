---
name: ENCRYPTION_KEY persistence
description: Why the vault's Fernet key must be a persistent secret, and how the app surfaces an unset key.
---

The vault (ApiKey rows) and SSH/Browserbase config are Fernet-encrypted with
ENCRYPTION_KEY. If unset, `src/web/crypto.py` auto-generates an EPHEMERAL key per
process — so every prod restart yields a new key and previously saved credentials
become undecryptable (silent data loss).

**Rule:** ENCRYPTION_KEY must be set as a persistent deployment secret, and must
NEVER be rotated/changed once credentials are saved — changing it strands all
existing ciphertext (no re-encryption path exists).

**Why:** autoscale prod restarts frequently; an ephemeral key looked fine in dev
(single long-lived process) but silently wiped saved keys in prod.

**How to apply:** `crypto.encryption_key_is_persistent()` returns False when the
key was auto-generated. The `/api/keys` and `/api/capabilities` responses carry
`encryption_persistent`; the React Keys + Capabilities pages render a red warning
banner when it's False. crypto module logs a loud WARNING at import too.
