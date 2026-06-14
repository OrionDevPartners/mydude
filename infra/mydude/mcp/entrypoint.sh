#!/usr/bin/env sh
# MyDude — Azure MCP dev-accelerator container entrypoint.
# Fail loud on misconfiguration BEFORE binding the server. Never prints secrets.
set -eu

echo "[entrypoint] mydude-azure-dev-accelerator starting"

# Control-plane id needed to resolve live ARM outputs (endpoints) + Key Vault.
if [ -z "${AZURE_SUBSCRIPTION_ID:-}" ]; then
  echo "[entrypoint] FATAL: AZURE_SUBSCRIPTION_ID is not set (needed to resolve Azure endpoints / Key Vault)." >&2
  exit 1
fi

# The server must be able to obtain its bearer token: an explicit token OR a Key
# Vault secret name to fetch under the managed identity. Presence only — the
# value is never read or printed here (governance pillar #3).
if [ -z "${AZURE_MCP_AUTH_TOKEN:-}" ] && [ -z "${AZURE_MCP_AUTH_SECRET_NAME:-}" ]; then
  echo "[entrypoint] FATAL: neither AZURE_MCP_AUTH_TOKEN nor AZURE_MCP_AUTH_SECRET_NAME is set; refusing to serve open." >&2
  exit 1
fi

echo "[entrypoint] preflight ok (subscription set; auth source present); launching server"
exec "$@"
