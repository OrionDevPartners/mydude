"""Attempt to register the resource providers the MyDude stack needs.

Provider registration is a subscription-scoped action. If the SP is only
RG-Owner, these calls will fail with an authorization error -- which tells
us a subscription admin must register them first.
"""
import os
import sys
import time

from azure.identity import ClientSecretCredential
from azure.mgmt.resource import ResourceManagementClient

NEEDED = [
    "Microsoft.DocumentDB",
    "Microsoft.Fabric",
    "Microsoft.KeyVault",
    "Microsoft.OperationalInsights",
    "Microsoft.Insights",
]


def main() -> int:
    cred = ClientSecretCredential(
        os.environ["AZURE_TENANT_ID"],
        os.environ["AZURE_CLIENT_ID"],
        os.environ["AZURE_CLIENT_SECRET"],
    )
    rmc = ResourceManagementClient(cred, os.environ["AZURE_SUBSCRIPTION_ID"])

    results = {}
    for ns in NEEDED:
        try:
            p = rmc.providers.register(ns)
            results[ns] = f"register accepted -> {p.registration_state}"
        except Exception as e:
            results[ns] = f"FAILED {type(e).__name__}: {str(e)[:200]}"

    print("=== register attempts ===")
    for ns, msg in results.items():
        print(f"   {ns:32s} {msg}")

    # Poll a short while for state transitions
    print("\n=== polling registration state (up to ~60s) ===")
    for _ in range(6):
        time.sleep(10)
        states = {}
        all_done = True
        for ns in NEEDED:
            try:
                states[ns] = rmc.providers.get(ns).registration_state
            except Exception as e:
                states[ns] = f"ERR {type(e).__name__}"
            if states[ns] != "Registered":
                all_done = False
        print("   " + "  ".join(f"{ns.split('.')[-1]}={st}" for ns, st in states.items()))
        if all_done:
            print("   ALL REGISTERED")
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
