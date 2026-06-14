"""Provision the Azure MCP dev-accelerator secrets in Key Vault.

Runs from INSIDE the Azure VNet (Key Vault is private), under an identity that
holds Key Vault `set` (e.g. `mydude-bcs-gate` or `mydude-foundry-agent`). It
mints two strong random secrets and stores them as Key Vault secrets:

  1. the MCP **bearer token** (clients authenticate to the server with it), and
  2. the two-phase deploy **token-signing secret** (the server signs/verifies the
     short-lived `azure_deploy_plan` -> `azure_deploy_apply` tokens with it; a
     STABLE secret is required so the plan->apply binding survives restarts /
     replicas — never an ephemeral per-process key).

The VALUEs are never printed or logged — only the secret names + versions.

The MCP container reads BOTH at runtime under its managed identity
(AZURE_MCP_AUTH_SECRET_NAME + AZURE_MCP_DEPLOY_SECRET_NAME). To configure an MCP
client, an operator retrieves the bearer token with THEIR OWN authenticated call:

    az keyvault secret show --vault-name mydude-kv --name azure-mcp-auth-token --query value -o tsv

(The deploy-token signing secret is never needed by a client — only by the server.)

Inputs (env, never hardcoded):
  AZURE_SUBSCRIPTION_ID    — to read live ARM outputs (Key Vault uri)

Usage:
    python3 infra/mydude/local/setup_mcp_token.py                 # create either if absent
    python3 infra/mydude/local/setup_mcp_token.py --rotate        # always mint new values
    python3 infra/mydude/local/setup_mcp_token.py --dry-run       # show plan, no writes
    python3 infra/mydude/local/setup_mcp_token.py --secret-name azure-mcp-auth-token
    python3 infra/mydude/local/setup_mcp_token.py --deploy-secret-name azure-mcp-deploy-token-secret
"""
from __future__ import annotations

import argparse
import secrets as _secrets
import sys

import azure_common as az

DEFAULT_SECRET_NAME = "azure-mcp-auth-token"
DEFAULT_DEPLOY_SECRET_NAME = "azure-mcp-deploy-token-secret"


def _ensure_secret(vault_uri: str, name: str, label: str, *, rotate: bool,
                   dry_run: bool) -> int:
    """Create/rotate a single high-entropy KV secret (value never printed)."""
    print("\n--- %s ---" % label)
    print("   secret name: %s" % name)
    try:
        existing = az.kv_get_secret(name, vault_uri)
    except Exception as e:  # noqa: BLE001 - fail loud on KV read error
        print("ERROR: could not query Key Vault:", str(e)[:200], file=sys.stderr)
        return 1

    if existing and not rotate:
        print("   already present — leaving unchanged (use --rotate to replace).")
        print("   (value withheld; retrieve it yourself with `az keyvault secret show`).")
        return 0

    verb = "rotated" if existing else "created"
    if dry_run:
        print("   [DRY RUN] Would set '%s' to %s (384-bit token; value withheld)." %
              (name, "a NEW value" if existing else "a fresh value"))
        return 0

    value = _secrets.token_urlsafe(48)  # 384-bit URL-safe random
    try:
        version = az.kv_set_secret(name, value, vault_uri)
    except Exception as e:  # noqa: BLE001 - fail loud on write error
        print("ERROR: failed to set secret '%s':" % name, str(e)[:200], file=sys.stderr)
        return 1
    finally:
        del value  # do not keep the value around in this process

    print("   %s '%s' version=%s (value never printed)." % (verb, name, version))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Provision the Azure MCP bearer + deploy-signing secrets in "
                    "Key Vault (values never printed)")
    ap.add_argument("--secret-name", default=DEFAULT_SECRET_NAME,
                    help="Key Vault secret name for the bearer token (default: %(default)s)")
    ap.add_argument("--deploy-secret-name", default=DEFAULT_DEPLOY_SECRET_NAME,
                    help="Key Vault secret name for the deploy-token signing secret "
                         "(default: %(default)s)")
    ap.add_argument("--rotate", action="store_true",
                    help="Mint new values even if the secrets already exist")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would happen without contacting Key Vault for writes")
    args = ap.parse_args()

    try:
        outputs = az.get_deployment_outputs()
        vault_uri = az.keyvault_uri(outputs)
    except az.AzureWiringError as e:
        print("ERROR:", e, file=sys.stderr)
        return 1

    print("=== Azure MCP secret setup ===")
    print("   vault: %s" % vault_uri)

    rc = _ensure_secret(vault_uri, args.secret_name, "MCP bearer token",
                        rotate=args.rotate, dry_run=args.dry_run)
    if rc != 0:
        return rc
    rc = _ensure_secret(vault_uri, args.deploy_secret_name,
                        "Two-phase deploy token-signing secret",
                        rotate=args.rotate, dry_run=args.dry_run)
    if rc != 0:
        return rc

    print("\nNext steps:")
    print("   - The MCP container reads both at startup/runtime under its identity:")
    print("       AZURE_MCP_AUTH_SECRET_NAME=%s" % args.secret_name)
    print("       AZURE_MCP_DEPLOY_SECRET_NAME=%s" % args.deploy_secret_name)
    print("   - To connect a client, retrieve the BEARER token with YOUR credentials:")
    print("       az keyvault secret show --vault-name <kv-name> --name %s --query value -o tsv" %
          args.secret_name)
    print("   - The deploy-signing secret is server-only — clients never need it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
