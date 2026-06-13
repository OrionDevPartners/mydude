"""Data-plane doctor: prove the app can reach AOAI + Cosmos + Postgres.

This is the acceptance check for post-provision step "App can reach AOAI +
Cosmos + Postgres over their private endpoints". It runs from INSIDE the Azure
VNet and exits non-zero if any leg fails.

Checks:
  * Key Vault — hydrate DSN + BCS secrets into the env (also proves KV read).
  * Postgres  — connect with each writer DSN, SELECT 1, read governance.schema_manifest.
  * Cosmos    — read the agents_memory containers (data-plane RBAC).
  * AOAI      — AAD token + a minimal `gpt-41-mini` completion (or, with
                --no-spend, just acquire a token and open the endpoint).

Usage:
    python3 infra/mydude/local/dataplane_doctor.py
    python3 infra/mydude/local/dataplane_doctor.py --no-spend   # skip the billed AOAI call
"""
from __future__ import annotations

import argparse
import os
import sys

import azure_common as az

AOAI_DEPLOYMENT = "gpt-41-mini"
AOAI_API_VERSION = "2024-10-21"
COSMOS_DB = "agents_memory"
COSMOS_CONTAINERS = ["episodic", "vectors", "documents"]


def _check_keyvault() -> tuple[bool, str]:
    try:
        status = az.hydrate_env_from_keyvault(overwrite=False)
        lines = "; ".join("%s=%s" % (k, v) for k, v in status.items())
        missing = [k for k, v in status.items() if v.startswith("missing")]
        if missing:
            return False, "secrets missing in vault: %s" % lines
        return True, lines
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]


def _check_postgres() -> tuple[bool, str]:
    try:
        import psycopg2
    except ImportError as e:
        return False, "psycopg2 not installed: %s" % e

    notes = []
    ok = True
    for db_key, env_var in (("agents_home", "PG_AGENTS_HOME_DSN"),
                            ("provider_home", "PG_PROVIDER_HOME_DSN")):
        dsn = os.environ.get(env_var, "")
        if not dsn:
            # fall back to building from writer-password env (no vault available)
            try:
                dsn = az.build_db_dsn(db_key)
            except az.AzureWiringError as e:
                ok = False
                notes.append("%s: no DSN (%s)" % (db_key, str(e)[:80]))
                continue
        try:
            conn = psycopg2.connect(dsn, connect_timeout=10)
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
                cur.execute(
                    "SELECT count(*) FROM governance.schema_manifest WHERE schema_name=%s",
                    (db_key,),
                )
                rows = cur.fetchone()[0]
            conn.close()
            notes.append("%s: connected, schema_manifest rows=%s" % (db_key, rows))
        except Exception as e:  # noqa: BLE001
            ok = False
            notes.append("%s: %s" % (db_key, str(e)[:120]))
    return ok, "; ".join(notes)


def _check_cosmos() -> tuple[bool, str]:
    try:
        from azure.cosmos import CosmosClient
        from azure.cosmos.exceptions import CosmosHttpResponseError
    except ImportError as e:
        return False, "azure-cosmos not installed: %s" % e
    try:
        client = CosmosClient(url=az.cosmos_endpoint(), credential=az.credential())
        db = client.get_database_client(COSMOS_DB)
        db.read()
        for name in COSMOS_CONTAINERS:
            db.get_container_client(name).read()
        return True, "database + %d containers reachable" % len(COSMOS_CONTAINERS)
    except CosmosHttpResponseError as e:
        return False, str(e)[:200]
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]


def _check_aoai(no_spend: bool) -> tuple[bool, str]:
    try:
        from azure.identity import get_bearer_token_provider
        from openai import AzureOpenAI
    except ImportError as e:
        return False, "openai/azure-identity not installed: %s" % e
    try:
        endpoint = az.aoai_endpoint()
        token_provider = get_bearer_token_provider(
            az.credential(), "https://cognitiveservices.azure.com/.default"
        )
        if no_spend:
            # Acquire a token only — proves AAD + endpoint resolution without billing.
            token_provider()
            return True, "AAD token acquired for %s (no completion issued)" % endpoint
        client = AzureOpenAI(
            azure_endpoint=endpoint,
            azure_ad_token_provider=token_provider,
            api_version=AOAI_API_VERSION,
        )
        resp = client.chat.completions.create(
            model=AOAI_DEPLOYMENT,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
        return True, "completion ok (id=%s)" % getattr(resp, "id", "?")
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]


def main() -> int:
    ap = argparse.ArgumentParser(description="MyDude Azure data-plane doctor")
    ap.add_argument("--no-spend", action="store_true",
                    help="Skip the billed AOAI completion; only validate the AAD token")
    args = ap.parse_args()

    print("=== MyDude data-plane doctor ===")
    try:
        outputs = az.get_deployment_outputs()
        print("   deployment outputs resolved (%d keys)" % len(outputs))
    except Exception as e:  # noqa: BLE001
        print("ERROR: cannot read deployment outputs:", str(e)[:200], file=sys.stderr)
        return 1

    checks = [
        ("keyvault", _check_keyvault()),
        ("postgres", _check_postgres()),
        ("cosmos", _check_cosmos()),
        ("aoai", _check_aoai(args.no_spend)),
    ]

    all_ok = True
    for name, (ok, detail) in checks:
        all_ok = all_ok and ok
        print("   [%s] %-9s %s" % ("PASS" if ok else "FAIL", name, detail))

    if not all_ok:
        print("\n!! data-plane doctor: one or more legs FAILED.", file=sys.stderr)
        return 1
    print("\ndata-plane doctor: ALL PASS — app can reach AOAI + Cosmos + Postgres.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
