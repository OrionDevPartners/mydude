---
name: bicep
description: Compile/build Azure Bicep templates in this Replit container. Use whenever you need to run `bicep build`, lint, or compile .bicep IaC to ARM JSON (e.g. the infra/mydude stack). Covers the two recurring gotchas — the bicep binary is wiped on every environment reset, and it fails without the globalization-invariant flag.
---

# Bicep in this Replit container

Two things bite every session. Handle both up front.

## Gotcha 1 — the bicep binary is NOT persistent
Environment resets wipe `~/.azure/bin/bicep`. It will be missing again next
session even if you installed it before. **Always ensure it exists before building.**

Reinstall with the nix-provided Azure CLI (there is no global `az` on PATH by default):

```bash
AZ="$(command -v az || ls /nix/store/*azure-cli*/bin/az 2>/dev/null | head -1)"
"$AZ" bicep install        # installs to ~/.azure/bin/bicep
```

## Gotcha 2 — bicep needs `DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1`
The container has no libicu/ICU. Without this env var the binary FailFasts with
`Couldn't find a valid ICU package installed on the system`. Set it on **every**
bicep invocation (the `az bicep install` download itself does not need it).

## Build / lint
```bash
BICEP="$HOME/.azure/bin/bicep"
DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 "$BICEP" build infra/mydude/bicep/main.bicep --outfile /tmp/main.json
# or print to stdout:
DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 "$BICEP" build infra/mydude/bicep/main.bicep --stdout
```
`build` exit 0 = success. Lint **warnings** (unused params, BCP081 on preview
resource types, hardcoded `core.windows.net` in a private-DNS-zone name) are
benign and do not block; only non-zero exit / `Error` lines are real failures.

## One-shot helper
`./.agents/skills/bicep/ensure-bicep.sh` installs bicep if missing and prints its
path. Typical use:
```bash
BICEP="$(.agents/skills/bicep/ensure-bicep.sh)"
DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 "$BICEP" build <file.bicep> --outfile /tmp/out.json
```

## Deploying compiled ARM JSON
Don't shell out to `az deployment` for the MyDude stack — use the Python driver
`infra/mydude/local/deploy.py` (validate | whatif | deploy), which already
compiles via this binary and authenticates with the `AZURE_*` service principal.
