"""Seed / verify the Cosmos DB working-memory containers.

This is post-provision step 3. The `agents_memory` database and its three
containers (`episodic`, `vectors`, `documents`) are created by Bicep
(infra/mydude/bicep/modules/cosmos.bicep) — including the diskANN vector index
on `vectors`. Container creation needs the management plane / Cosmos DB
Operator role, so this script does NOT create containers: it VERIFIES they
exist (failing loud if Bicep did not run) and then writes + reads back a small
probe document in each, proving the app's data-plane RBAC works.

Cosmos key auth is disabled (AAD only), so this authenticates with
`azure_common.credential()`. The identity must hold "Cosmos DB Built-in Data
Contributor" (the deployed `mydude-agents-home-db` identity does).

Runs from INSIDE the Azure VNet (Cosmos public access is disabled + PE).

Usage:
    python3 infra/mydude/local/seed_cosmos.py             # verify + probe write/read
    python3 infra/mydude/local/seed_cosmos.py --verify-only   # read-only, no writes
"""
from __future__ import annotations

import argparse
import datetime
import sys

import azure_common as az

DATABASE = "agents_memory"
# container -> (partition key path, probe partition value)
CONTAINERS = {
    "episodic": ("/agentId", "__seed_probe__"),
    "vectors": ("/namespace", "__seed_probe__"),
    "documents": ("/namespace", "__seed_probe__"),
}
PROBE_ID = "__seed_probe__"


def _probe_doc(container: str, pk_path: str, pk_value: str) -> dict:
    pk_field = pk_path.lstrip("/")
    return {
        "id": PROBE_ID,
        pk_field: pk_value,
        "_probe": True,
        "container": container,
        "note": "post-provision seed/verify probe; safe to delete",
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed/verify Cosmos containers for the MyDude stack")
    ap.add_argument("--verify-only", action="store_true",
                    help="Only verify the containers exist; do not write probe docs")
    args = ap.parse_args()

    try:
        from azure.cosmos import CosmosClient
        from azure.cosmos.exceptions import CosmosResourceNotFoundError, CosmosHttpResponseError
    except ImportError as e:
        print("ERROR: azure-cosmos is not installed:", e, file=sys.stderr)
        return 1

    try:
        endpoint = az.cosmos_endpoint()
    except az.AzureWiringError as e:
        print("ERROR:", e, file=sys.stderr)
        return 1

    print("=== Cosmos seed/verify ===")
    print("   endpoint: %s" % endpoint)
    print("   database: %s" % DATABASE)

    client = CosmosClient(url=endpoint, credential=az.credential())

    failures = []
    try:
        db = client.get_database_client(DATABASE)
        db.read()  # fail loud if the database is missing
    except CosmosResourceNotFoundError:
        print("ERROR: database '%s' not found. Run the Bicep deploy (cosmos.bicep)." % DATABASE,
              file=sys.stderr)
        return 1
    except CosmosHttpResponseError as e:
        print("ERROR: cannot reach Cosmos (private endpoint?). %s" % (str(e)[:300]), file=sys.stderr)
        return 1

    for name, (pk_path, pk_value) in CONTAINERS.items():
        try:
            container = db.get_container_client(name)
            container.read()  # verify existence
        except CosmosResourceNotFoundError:
            failures.append((name, "container missing — create it via cosmos.bicep"))
            print("   FAIL %-10s container missing" % name, file=sys.stderr)
            continue
        except CosmosHttpResponseError as e:
            failures.append((name, str(e)[:200]))
            print("   FAIL %-10s %s" % (name, str(e)[:200]), file=sys.stderr)
            continue

        if args.verify_only:
            print("   ok   %-10s exists (pk=%s)" % (name, pk_path))
            continue

        try:
            container.upsert_item(_probe_doc(name, pk_path, pk_value))
            read_back = container.read_item(item=PROBE_ID, partition_key=pk_value)
            assert read_back.get("id") == PROBE_ID
            print("   ok   %-10s probe write+read OK (pk=%s)" % (name, pk_path))
        except Exception as e:  # noqa: BLE001
            failures.append((name, str(e)[:200]))
            print("   FAIL %-10s probe write/read failed: %s" % (name, str(e)[:200]),
                  file=sys.stderr)

    if failures:
        print("\n!! %d container check(s) failed." % len(failures), file=sys.stderr)
        return 1
    print("\nCosmos containers verified%s." % ("" if args.verify_only else " and seeded"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
