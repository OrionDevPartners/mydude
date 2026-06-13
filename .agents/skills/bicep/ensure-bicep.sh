#!/usr/bin/env bash
# Ensure the bicep CLI is installed in this (non-persistent) Replit container.
# Prints the path to the bicep binary on stdout. Remember: every bicep invocation
# still needs DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 (no ICU in the container).
set -euo pipefail
BICEP="$HOME/.azure/bin/bicep"
if [ ! -x "$BICEP" ]; then
  AZ="$(command -v az || ls /nix/store/*azure-cli*/bin/az 2>/dev/null | head -1)"
  if [ -z "${AZ:-}" ]; then
    echo "ERROR: could not locate the Azure CLI to run 'az bicep install'." >&2
    exit 1
  fi
  "$AZ" bicep install >&2
fi
echo "$BICEP"
