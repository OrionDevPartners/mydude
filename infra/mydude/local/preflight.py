"""Read-only Azure preflight for the MyDude stack deploy.

Confirms SP auth, target RG, existing resources, resource-provider
registration, and Azure OpenAI quota in the target region. No writes.
"""
import os
import sys

from azure.identity import ClientSecretCredential
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.cognitiveservices import CognitiveServicesManagementClient

LOCATION = "eastus2"
RG_CANDIDATES = ["mydude", "MyDude"]
PROVIDERS = [
    "Microsoft.DocumentDB",
    "Microsoft.Fabric",
    "Microsoft.CognitiveServices",
    "Microsoft.DBforPostgreSQL",
    "Microsoft.Network",
    "Microsoft.Storage",
    "Microsoft.KeyVault",
    "Microsoft.OperationalInsights",
    "Microsoft.ManagedIdentity",
    "Microsoft.Insights",
]


def main() -> int:
    tenant = os.environ["AZURE_TENANT_ID"]
    client_id = os.environ["AZURE_CLIENT_ID"]
    client_secret = os.environ["AZURE_CLIENT_SECRET"]
    sub = os.environ["AZURE_SUBSCRIPTION_ID"]

    cred = ClientSecretCredential(tenant, client_id, client_secret)
    rmc = ResourceManagementClient(cred, sub)

    print(f"=== Subscription {sub[:8]}... ===")

    # Resolve RG (casing unknown)
    rg_name = None
    rg_loc = None
    for cand in RG_CANDIDATES:
        try:
            rg = rmc.resource_groups.get(cand)
            rg_name = rg.name
            rg_loc = rg.location
            print(f"RG FOUND: name={rg.name} location={rg.location} state={rg.properties.provisioning_state}")
            break
        except Exception as e:
            print(f"RG '{cand}' not found ({type(e).__name__})")
    if not rg_name:
        print("!! No target RG found under either casing. Listing all RGs visible to SP:")
        try:
            for g in rmc.resource_groups.list():
                print(f"   - {g.name} ({g.location})")
        except Exception as e:
            print(f"   (cannot list RGs: {e})")

    # Existing resources in RG
    if rg_name:
        print(f"\n=== Existing resources in RG {rg_name} ===")
        try:
            items = list(rmc.resources.list_by_resource_group(rg_name))
            if not items:
                print("   (empty)")
            for r in items:
                print(f"   - {r.type}  {r.name}  [{r.location}]")
        except Exception as e:
            print(f"   (cannot list resources: {e})")

    # Provider registration
    print("\n=== Resource provider registration ===")
    for ns in PROVIDERS:
        try:
            p = rmc.providers.get(ns)
            print(f"   {ns:35s} {p.registration_state}")
        except Exception as e:
            print(f"   {ns:35s} ERROR {type(e).__name__}: {e}")

    # Azure OpenAI quota in region
    print(f"\n=== Azure OpenAI / CognitiveServices usage in {LOCATION} ===")
    try:
        csm = CognitiveServicesManagementClient(cred, sub)
        usages = list(csm.usages.list(LOCATION))
        oa = [u for u in usages if u.name and (
            "OpenAI" in (u.name.value or "") or "gpt" in (u.name.value or "").lower()
        )]
        if not oa:
            print("   (no OpenAI-specific usage entries returned; printing all non-zero limits)")
            oa = [u for u in usages if (u.limit or 0) > 0][:40]
        for u in oa:
            name = u.name.value if u.name else "?"
            print(f"   {name:55s} current={u.current_value} limit={u.limit}")
    except Exception as e:
        print(f"   (cannot read CognitiveServices usages: {type(e).__name__}: {e})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
