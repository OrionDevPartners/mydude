"""Azure MCP dev-accelerator doctor: prove the governed MCP container app is
deployed, private, and correctly wired — WITHOUT ever printing a secret value.

Runs from anywhere that can reach Azure ARM with the mydude service principal /
managed identity (control-plane reads only). It does NOT call the MCP server
itself (that endpoint is private to the VNet); it validates the deployed
configuration and the presence of the bearer-token secret in Key Vault.

Checks:
  * ARM outputs   — the mydude-stack deployment resolves (sub id + outputs).
  * Key Vault     — the bearer-token secret EXISTS (presence only; value withheld).
  * Managed Env   — exists and is VNet-internal.
  * Container App — exists; ingress is INTERNAL (external=false); runs as a
                    user-assigned identity; ENABLE_AZURE_MCP=true and
                    AZURE_MCP_AUTH_SECRET_NAME set; reports deploy-apply gate.

Usage:
    python3 infra/mydude/local/azure_mcp_doctor.py
    python3 infra/mydude/local/azure_mcp_doctor.py --secret-name azure-mcp-auth-token
"""
from __future__ import annotations

import argparse
import sys

import azure_common as az

APP_API_VERSION = "2024-03-01"
DEFAULT_SECRET_NAME = "azure-mcp-auth-token"
APP_NAME = "mydude-azure-mcp"
ENV_NAME = "mydude-mcp-env"


def _rmc():
    from azure.mgmt.resource import ResourceManagementClient

    return ResourceManagementClient(az.credential(), az.subscription_id())


def _resource_id(rtype: str, name: str) -> str:
    return "/subscriptions/%s/resourceGroups/%s/providers/%s/%s" % (
        az.subscription_id(), az.RG_NAME, rtype, name)


def _check_keyvault_token(secret_name: str) -> tuple[bool, str]:
    try:
        vault_uri = az.keyvault_uri()
        val = az.kv_get_secret(secret_name, vault_uri)
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]
    if not val:
        return False, "secret '%s' is ABSENT — run setup_mcp_token.py" % secret_name
    return True, "secret '%s' present (value withheld)" % secret_name


def _check_managed_env(rmc) -> tuple[bool, str]:
    try:
        res = rmc.resources.get_by_id(
            _resource_id("Microsoft.App/managedEnvironments", ENV_NAME), APP_API_VERSION)
    except Exception as e:  # noqa: BLE001
        return False, "managed environment '%s' not found: %s" % (ENV_NAME, str(e)[:140])
    props = res.properties or {}
    internal = (props.get("vnetConfiguration") or {}).get("internal", False)
    return (bool(internal),
            "vnet-internal" if internal else "managed env is NOT vnet-internal")


def _check_container_app(rmc) -> tuple[bool, str]:
    try:
        res = rmc.resources.get_by_id(
            _resource_id("Microsoft.App/containerApps", APP_NAME), APP_API_VERSION)
    except Exception as e:  # noqa: BLE001
        return False, "container app '%s' not found / unreadable: %s" % (APP_NAME, str(e)[:140])
    props = res.properties or {}
    notes = []
    ok = True

    ingress = (props.get("configuration") or {}).get("ingress") or {}
    if ingress.get("external", True):
        ok = False
        notes.append("ingress is EXTERNAL (must be internal)")
    else:
        notes.append("ingress internal")

    identity = getattr(res, "identity", None)
    itype = ""
    if isinstance(identity, dict):
        itype = identity.get("type", "") or ""
    elif identity is not None:
        itype = getattr(identity, "type", "") or ""
    if "UserAssigned" not in itype:
        ok = False
        notes.append("identity is not UserAssigned (%s)" % (itype or "none"))
    else:
        notes.append("user-assigned identity")

    env = {}
    for c in ((props.get("template") or {}).get("containers") or []):
        for kv in (c.get("env") or []):
            if "name" in kv:
                env[kv["name"]] = kv.get("value")
    if env.get("ENABLE_AZURE_MCP") != "true":
        ok = False
        notes.append("ENABLE_AZURE_MCP != true")
    if not env.get("AZURE_MCP_AUTH_SECRET_NAME"):
        ok = False
        notes.append("AZURE_MCP_AUTH_SECRET_NAME unset")
    notes.append("deploy-apply %s" %
                 ("ENABLED" if env.get("ALLOW_AZURE_DEPLOY") == "true" else "default-deny"))

    # Advisory (non-fatal): once the app FQDN is known, the DNS-rebinding host
    # check should be pinned to it (AZURE_MCP_ALLOWED_HOSTS) and the opt-out
    # dropped. Internal ingress + bearer auth already guard the endpoint, so a
    # disabled host check is acceptable, not a hole — but pinning it is cheap
    # defense-in-depth the architect recommended. Warn so it isn't forgotten.
    allowed_hosts = (env.get("AZURE_MCP_ALLOWED_HOSTS") or "").strip()
    host_check_disabled = (env.get("AZURE_MCP_DISABLE_HOST_CHECK") or "").strip().lower() \
        in {"1", "true", "yes", "on"}
    if not allowed_hosts or host_check_disabled:
        notes.append("WARN host check not pinned — set AZURE_MCP_ALLOWED_HOSTS "
                     "to the app FQDN and drop AZURE_MCP_DISABLE_HOST_CHECK")
    else:
        notes.append("host check pinned")

    return ok, "; ".join(notes)


def main() -> int:
    ap = argparse.ArgumentParser(description="MyDude Azure MCP dev-accelerator doctor")
    ap.add_argument("--secret-name", default=DEFAULT_SECRET_NAME,
                    help="Key Vault bearer-token secret name (default: %(default)s)")
    args = ap.parse_args()

    print("=== MyDude Azure MCP doctor ===")
    try:
        outputs = az.get_deployment_outputs()
        print("   deployment outputs resolved (%d keys)" % len(outputs))
    except Exception as e:  # noqa: BLE001
        print("ERROR: cannot read deployment outputs:", str(e)[:200], file=sys.stderr)
        return 1

    try:
        rmc = _rmc()
    except Exception as e:  # noqa: BLE001
        print("ERROR: cannot create ARM client:", str(e)[:200], file=sys.stderr)
        return 1

    checks = [
        ("keyvault-token", _check_keyvault_token(args.secret_name)),
        ("managed-env", _check_managed_env(rmc)),
        ("container-app", _check_container_app(rmc)),
    ]
    all_ok = True
    for name, (ok, detail) in checks:
        all_ok = all_ok and ok
        print("   [%s] %-15s %s" % ("PASS" if ok else "FAIL", name, detail))

    if not all_ok:
        print("\n!! azure-mcp doctor: one or more checks FAILED.", file=sys.stderr)
        return 1
    print("\nazure-mcp doctor: ALL PASS — governed MCP container app is private + wired.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
