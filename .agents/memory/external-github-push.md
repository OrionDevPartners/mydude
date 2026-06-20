---
name: External GitHub push from Replit container
description: Why pushing to a personal GitHub repo with a PAT fails ("Invalid username or token") in the Replit workspace, and the robust fix.
---

# Pushing to an external GitHub repo from the Replit workspace

The Replit container forces `GIT_ASKPASS=replit-git-askpass` (and `GIT_TERMINAL_PROMPT=0`) in the environment. The git configs are otherwise clean — no credential helper, no `insteadOf` rewrite (global config lives at `$GIT_CONFIG_GLOBAL`, not `~/.gitconfig`).

**Symptom:** `git push https://<PAT>@github.com/<owner>/<repo>.git main` fails with:
`remote: Invalid username or token. Password authentication is not supported for Git operations.`

**Why:** The bare `https://<TOKEN>@host` form puts the token in the *username* slot with no password. Git then calls `replit-git-askpass` to supply the missing password, which returns Replit's own GitHub credential (a different identity than the user's PAT). GitHub rejects the mismatched pair. `curl -u "<TOKEN>:"` succeeds in the same situation because it sends an explicit (empty) password and never asks.

**Fix — send the token explicitly so git never calls askpass:**
- URL form with BOTH user+pass: `https://x-access-token:<TOKEN>@github.com/<owner>/<repo>.git`
- Or header form (CI-style, most robust): `git -c credential.helper= -c "http.extraheader=Authorization: Basic $(printf 'x-access-token:%s' "$TOKEN" | base64 | tr -d '\n')" push https://github.com/<owner>/<repo>.git main`

**Verify auth independently (no git):** `curl -s -o /dev/null -w '%{http_code}' -u "x-access-token:$TOKEN" "https://github.com/<owner>/<repo>.git/info/refs?service=git-receive-pack"` → 200 means push auth works. (`git-upload-pack` = fetch, `git-receive-pack` = push.) Also check repo access + push permission via `GET /repos/{owner}/{repo}` (`permissions.push`).

**Agent constraints that shaped this:** the agent cannot run git itself (platform-managed VC), so it hands the user copy-paste commands or a small script (`.local/push_to_github.sh`) that reads the PAT from the `Github_PAT` secret at runtime. A long-running user shell opened *before* the secret was added won't have it — open a new shell tab (Replit injects current secrets into new shells). Compare the shell's token to the live secret with a tail/SHA fingerprint, never by printing it.

**Secret-handling caution:** `env | grep -i '^GIT'` ALSO matches a `Github_PAT`-style var name and dumps the secret to logs. Use a precise pattern like `env | grep -E '^GIT_'` when inspecting git env. If a token leaks into logs/scrollback, advise rotating it.
