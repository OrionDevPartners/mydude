"""Populate Key Vault with the Postgres DSNs and the BCS idempotency key.

This is post-provision step 1. It runs from INSIDE the Azure VNet (the services
are private), using an identity that holds Key Vault `set` (e.g. the
`mydude-bcs-gate` managed identity, which has get/set/list).

What it writes (names only — values never printed or committed):
  * `agents-home-pg-dsn`   — writer DSN for the `agents_home` database
  * `provider-home-pg-dsn` — writer DSN for the `provider_home` database
  * `bcs-idempotency-key`  — the BCS lease/idempotency secret (hydrated into
                             BCS_LEASE_SECRET at runtime)

Inputs (env, never hardcoded):
  AZURE_SUBSCRIPTION_ID                 — to read live ARM outputs (KV uri, PG fqdn)
  PG_AGENTS_HOME_WRITER_PASSWORD        — agents_home_writer role password
  PG_PROVIDER_HOME_WRITER_PASSWORD      — provider_home_writer role password
  BCS_IDEMPOTENCY_KEY (optional)        — reuse an existing value; if absent a
                                          strong random one is generated

Usage:
    python3 infra/mydude/local/populate_keyvault.py            # set all
    python3 infra/mydude/local/populate_keyvault.py --dry-run  # show plan, no writes
"""
from __future__ import annotations

import argparse
import secrets as _secrets
import sys

import azure_common as az


def _resolve_bcs_idempotency_key(vault_uri: str) -> tuple[str, str]:
    """Return (value, origin). Prefer env, then existing vault value, else mint one."""
    import os

    env_val = os.environ.get("BCS_IDEMPOTENCY_KEY", "")
    if env_val:
        return env_val, "env BCS_IDEMPOTENCY_KEY"
    existing = az.kv_get_secret(az.KV_BCS_IDEMPOTENCY_KEY, vault_uri)
    if existing:
        return existing, "existing vault value (left unchanged)"
    return _secrets.token_urlsafe(48), "generated (256-bit)"


def main() -> int:
    ap = argparse.ArgumentParser(description="Populate Key Vault for the MyDude stack")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would be written without contacting Key Vault for writes")
    args = ap.parse_args()

    try:
        outputs = az.get_deployment_outputs()
        vault_uri = az.keyvault_uri(outputs)
        fqdn = az.postgres_fqdn(outputs)
    except az.AzureWiringError as e:
        print("ERROR:", e, file=sys.stderr)
        return 1

    print("=== Key Vault population ===")
    print("   vault:    %s" % vault_uri)
    print("   pg fqdn:  %s" % fqdn)

    # Build the two DSNs (raises loudly if a writer password env is missing).
    try:
        dsns = {
            az.DB_DSN_SECRET["agents_home"]: az.build_db_dsn("agents_home", outputs),
            az.DB_DSN_SECRET["provider_home"]: az.build_db_dsn("provider_home", outputs),
        }
    except az.AzureWiringError as e:
        print("ERROR:", e, file=sys.stderr)
        return 1

    bcs_value, bcs_origin = _resolve_bcs_idempotency_key(vault_uri)
    print("   bcs key:  %s" % bcs_origin)

    plan = list(dsns.keys()) + [az.KV_BCS_IDEMPOTENCY_KEY]
    if args.dry_run:
        print("\n[DRY RUN] Would set these secrets (values withheld):")
        for name in plan:
            print("   - %s" % name)
        return 0

    failures = []
    for name, value in dsns.items():
        try:
            version = az.kv_set_secret(name, value, vault_uri)
            print("   set %-22s version=%s" % (name, version))
        except Exception as e:  # noqa: BLE001 - fail loud per secret
            failures.append((name, str(e)))
            print("   FAIL %-22s %s" % (name, str(e)[:200]), file=sys.stderr)

    try:
        version = az.kv_set_secret(az.KV_BCS_IDEMPOTENCY_KEY, bcs_value, vault_uri)
        print("   set %-22s version=%s" % (az.KV_BCS_IDEMPOTENCY_KEY, version))
    except Exception as e:  # noqa: BLE001
        failures.append((az.KV_BCS_IDEMPOTENCY_KEY, str(e)))
        print("   FAIL %-22s %s" % (az.KV_BCS_IDEMPOTENCY_KEY, str(e)[:200]), file=sys.stderr)

    if failures:
        print("\n!! %d secret(s) failed to set." % len(failures), file=sys.stderr)
        return 1
    print("\nAll Key Vault secrets populated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
