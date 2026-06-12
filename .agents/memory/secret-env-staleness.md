---
name: Secret/env staleness in the agent shell
description: Why the agent's bash sees stale secret values, how to read the live value from a running workflow, and the Gemini disabled-API gotcha.
---

# Agent bash env is snapshotted — verify secrets via the running workflow

The agent's `bash` tool environment is captured at session start and does NOT
pick up Replit Secret changes the user makes mid-session. Re-running a script in
bash after the user "adds a new key" can keep using the OLD value.

**The running workflow process is the source of truth** — it gets fresh secrets
on restart. To read the value a restarted workflow actually has, parse its
`/proc/<PID>/environ` (NUL-separated `KEY=VALUE`). Find the pid with
`pgrep -f "python main.py"`.

**Verify without leaking:** never print a secret. Compare a SHA-256 prefix
fingerprint (e.g. `hashlib.sha256(v.encode()).hexdigest()[:12]`) across bash and
`/proc`. Identical fingerprint after the user "changed" the key ⇒ the stored
secret value never actually changed (user updated the wrong place, or generated a
new key bound to the same project).

**To run a one-off script with the live key:** source it from the workflow's
`/proc/<PID>/environ` into an exported env var, then run python — value stays out
of stdout.

## Gemini "PermissionDenied ... API has not been used in project N" gotcha
A valid `AIza…` key can still fail every call if the **Generative Language API is
disabled** on its Google Cloud project. The error names the project number. The
fix is to ENABLE that API for the project (console: `generativelanguage.googleapis.com`),
not to mint another key — a new key created *inside the same project* inherits the
disabled state. AI Studio's "Create API key in a new project" sidesteps it because
the new project has the API auto-enabled.
