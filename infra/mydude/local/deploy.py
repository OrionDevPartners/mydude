"""Validate / what-if / deploy the MyDude Azure stack against the EXISTING RG `mydude`.

Usage:
    python infra/mydude/local/deploy.py validate       # ARM validate (no cost)
    python infra/mydude/local/deploy.py whatif         # what-if preview (no cost)
    python infra/mydude/local/deploy.py deploy --yes   # BILLABLE create_or_update

Auth: ClientSecretCredential from the AZURE_* env secrets. The service principal is
RG-Owner on `mydude` ONLY, so this script never creates the RG and never performs
subscription-scoped operations. `tenantId` and `pgAdminPassword` are injected at
runtime from secrets and are never written to the repo.

Safety: every action first checks resource-provider registration. If any required
provider is NotRegistered the script aborts BEFORE compiling or calling ARM, so it
can never start a billable deploy while the subscription is not ready.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

from azure.identity import ClientSecretCredential
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.resource.deployments import DeploymentsMgmtClient
from azure.core.exceptions import HttpResponseError

HERE = os.path.dirname(os.path.abspath(__file__))
BICEP_DIR = os.path.normpath(os.path.join(HERE, "..", "bicep"))
MAIN_BICEP = os.path.join(BICEP_DIR, "main.bicep")
PARAMS_JSON = os.path.join(BICEP_DIR, "parameters.json")
RG_NAME = "mydude"
DEPLOYMENT_NAME = "mydude-stack"
NEEDED_PROVIDERS = [
    "Microsoft.DocumentDB",
    "Microsoft.Fabric",
    "Microsoft.KeyVault",
    "Microsoft.OperationalInsights",
    "Microsoft.Insights",
    "Microsoft.CognitiveServices",
    "Microsoft.DBforPostgreSQL",
    "Microsoft.Network",
    "Microsoft.Storage",
    "Microsoft.ManagedIdentity",
]


def _bicep_path() -> str:
    for cand in (os.path.expanduser("~/.azure/bin/bicep"),
                 "/home/runner/.azure/bin/bicep"):
        if os.path.exists(cand):
            return cand
    return shutil.which("bicep") or "bicep"


def compile_template() -> dict:
    out = os.path.join(tempfile.gettempdir(), "mydude_main.json")
    env = dict(os.environ, DOTNET_SYSTEM_GLOBALIZATION_INVARIANT="1")
    subprocess.run(
        [_bicep_path(), "build", MAIN_BICEP, "--outfile", out],
        check=True, env=env,
    )
    with open(out) as f:
        return json.load(f)


def validate_mcp_posture(params: dict) -> list:
    """Fail-loud invariants for the Azure MCP ingress posture (pillars #1 / #4).

    Public ingress makes the bearer token the SOLE network gate, so it MUST be
    paired with host pinning: a public app with an empty allow-list would expose
    the default FQDN with DNS-rebinding (Host-header) protection OFF. A custom
    domain only makes sense on public ingress and must itself appear in the
    allow-list so the server pins it. Returns a list of human-readable
    violations (empty == OK) so callers can fail loud BEFORE a billable deploy.
    """
    def _val(key, default):
        spec = params.get(key)
        return spec.get("value", default) if isinstance(spec, dict) else default

    external = bool(_val("azureMcpExternalIngress", False))
    domain = (_val("azureMcpCustomDomain", "") or "").strip()
    hosts_raw = _val("azureMcpAllowedHosts", "") or ""
    hosts = {h.strip().lower() for h in hosts_raw.split(",") if h.strip()}

    problems = []
    if external and not hosts:
        problems.append(
            "azureMcpExternalIngress=true exposes the MCP server publicly, so "
            "azureMcpAllowedHosts MUST pin the host(s) (DNS-rebinding hardening) "
            "— it is empty."
        )
    if domain:
        if not external:
            problems.append(
                f"azureMcpCustomDomain={domain!r} requires azureMcpExternalIngress=true "
                "— a custom domain cannot bind to internal-only ingress."
            )
        if domain.lower() not in hosts:
            problems.append(
                f"azureMcpCustomDomain={domain!r} must also appear in "
                "azureMcpAllowedHosts so the server pins the public host — it is missing."
            )
    return problems


def load_parameters() -> dict:
    with open(PARAMS_JSON) as f:
        params = json.load(f)["parameters"]
    # Inject secrets at runtime — never committed to the repo.
    params["tenantId"] = {"value": os.environ["AZURE_TENANT_ID"]}
    params["pgAdminPassword"] = {"value": os.environ["PG_ADMIN_PASSWORD"]}
    # Guard against any unfilled placeholder slipping into a deploy.
    leftovers = []
    for key, spec in params.items():
        val = spec.get("value")
        if isinstance(val, str) and ("REPLACE" in val or val.startswith("{")):
            leftovers.append(key)
        elif isinstance(val, list) and any(
            isinstance(x, str) and "REPLACE" in x for x in val
        ):
            leftovers.append(key)
    if leftovers:
        print(f"!! Unfilled parameters: {leftovers}")
        sys.exit(2)
    # Fail loud BEFORE any billable/irreversible deploy if the MCP ingress
    # posture would expose the server publicly without a host pin.
    posture = validate_mcp_posture(params)
    if posture:
        print("!! MCP ingress posture invalid (fail-loud; governance pillar #4):")
        for p in posture:
            print(f"   - {p}")
        sys.exit(2)
    return params


def check_providers(rmc: ResourceManagementClient) -> list:
    # Only "NotRegistered" is a hard block (the SP could not register at all).
    # "Registering" means registration was accepted and is finalizing — Azure's
    # top-level state oscillates between Registering/Registered for a while after
    # a provider is usable, so we let ARM validate be the authoritative check.
    bad = []
    for ns in NEEDED_PROVIDERS:
        state = rmc.providers.get(ns).registration_state
        flag = "   <== NOT REGISTERED" if state == "NotRegistered" else ""
        print(f"   {ns:35s} {state}{flag}")
        if state == "NotRegistered":
            bad.append(ns)
    return bad


def show_status(dmc: DeploymentsMgmtClient) -> int:
    from collections import Counter

    try:
        dep = dmc.deployments.get(RG_NAME, DEPLOYMENT_NAME)
    except HttpResponseError as e:
        print("no deployment found / error:", getattr(e, "message", str(e)))
        return 1
    state = dep.properties.provisioning_state
    print(f"deployment '{DEPLOYMENT_NAME}' state: {state}")

    counts = Counter()
    failed = []
    for op in dmc.deployment_operations.list(RG_NAME, DEPLOYMENT_NAME):
        p = op.properties
        st = p.provisioning_state or "?"
        counts[st] += 1
        if st == "Failed":
            tr = p.target_resource
            rid = getattr(tr, "id", "?") if tr else "?"
            failed.append((rid, getattr(p, "status_message", None)))
    print("   operation states:", dict(counts))
    for rid, msg in failed:
        print(f"   FAILED: {rid}")
        if msg:
            print(f"           {msg}")

    if state in ("Succeeded",):
        for key, spec in (dep.properties.outputs or {}).items():
            print(f"   output {key} = {spec.get('value')}")
        return 0
    if state in ("Failed", "Canceled"):
        return 1
    return 0  # still running


def main() -> int:
    ap = argparse.ArgumentParser(description="MyDude Azure deploy driver")
    ap.add_argument("action", choices=["validate", "whatif", "deploy", "status"])
    ap.add_argument("--yes", action="store_true", help="confirm a BILLABLE deploy")
    ap.add_argument(
        "--no-wait",
        action="store_true",
        help="submit the deploy and return immediately (poll later with `status`)",
    )
    args = ap.parse_args()

    cred = ClientSecretCredential(
        os.environ["AZURE_TENANT_ID"],
        os.environ["AZURE_CLIENT_ID"],
        os.environ["AZURE_CLIENT_SECRET"],
    )
    sub = os.environ["AZURE_SUBSCRIPTION_ID"]
    rmc = ResourceManagementClient(cred, sub)
    # azure-mgmt-resource >=25 split deployments into its own client/package
    # (azure-mgmt-resource-deployments). ResourceManagementClient no longer has
    # a `.deployments` op group, so deployment actions go through this client.
    dmc = DeploymentsMgmtClient(cred, sub)

    if args.action == "status":
        return show_status(dmc)

    print("=== resource provider registration ===")
    bad = check_providers(rmc)
    if bad:
        print(f"\n!! BLOCKED: {len(bad)} provider(s) NotRegistered.")
        print("   A subscription admin must register them before validate/deploy:")
        for ns in bad:
            print(f"     az provider register --namespace {ns}")
        return 1

    if args.action == "deploy" and not args.yes:
        print("\n!! 'deploy' BILLS real money. Re-run with --yes to confirm.")
        return 2

    print("\n=== compiling bicep -> ARM JSON ===")
    template = compile_template()
    params = load_parameters()
    print(f"   template resource blocks: {len(template.get('resources', []))}")

    payload = {
        "properties": {
            "mode": "Incremental",
            "template": template,
            "parameters": params,
        }
    }

    try:
        if args.action == "validate":
            print("\n=== ARM validate ===")
            dmc.deployments.begin_validate(RG_NAME, DEPLOYMENT_NAME, payload).result()
            print("VALIDATION OK")
            return 0

        if args.action == "whatif":
            print("\n=== what-if ===")
            res = dmc.deployments.begin_what_if(RG_NAME, DEPLOYMENT_NAME, payload).result()
            for ch in (res.changes or []):
                print(f"   {ch.change_type:12s} {ch.resource_id}")
            print(f"   total changes: {len(res.changes or [])}")
            return 0

        if args.action == "deploy":
            print("\n=== DEPLOY (BILLABLE) ===")
            poller = dmc.deployments.begin_create_or_update(
                RG_NAME, DEPLOYMENT_NAME, payload
            )
            if args.no_wait:
                # Initial PUT accepted; the deployment now runs server-side.
                # Poll progress with: python deploy.py status
                print("DEPLOYMENT SUBMITTED (running server-side).")
                print("   poll with: python3 infra/mydude/local/deploy.py status")
                return 0
            res = poller.result()
            print("DEPLOYMENT STATE:", res.properties.provisioning_state)
            for key, spec in (res.properties.outputs or {}).items():
                print(f"   output {key} = {spec.get('value')}")
            return 0
    except HttpResponseError as e:
        print("AZURE ERROR:")
        print(getattr(e, "message", str(e)))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
