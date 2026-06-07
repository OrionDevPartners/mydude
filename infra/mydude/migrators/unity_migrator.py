#!/usr/bin/env python3
"""MyDude Unity/Iceberg Migrator.

Applies the Unity Catalog ledger and table schemas (Managed Iceberg REST),
committed via the catalog. Each change is submitted through the BCS scope-
completion gate (V1-V7).

AUTHORITY BOUNDARY: This migrator never touches Postgres DDL.
All emitted CompletionClaims have authority=MigrationAuthority.UNITY.
The Postgres migrator's claims have authority=POSTGRES — they are
separate migration lineages that must never cross.

The BCS gate (single Entra managed identity) holds Unity Catalog write.
This migrator presents the claim; the gate performs the actual catalog write.

Usage:
    python unity_migrator.py --catalog mydude
    python unity_migrator.py --catalog mydude --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid

from base import (
    CompletionClaim,
    MigrationAuthority,
    ScopeGate,
    ScopeLabel,
    submit_completion_claim,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("unity_migrator")

# ---------------------------------------------------------------------------
# Unity Catalog ledger schema definitions
# Each table definition is versioned; content_hash is derived from the spec.
# ---------------------------------------------------------------------------
UNITY_SCHEMA_V001 = {
    "catalog": "mydude",
    "version": "V001",
    "description": "Initial MyDude Unity Catalog ledger schema",
    "schemas": [
        {
            "name": "agents_home_ledger",
            "comment": "Committed routing decisions + claim ledger (read-only projection from agents_home)",
            "tables": [
                {
                    "name": "claim_ledger",
                    "comment": "Immutable record of all BCS-promoted claims",
                    "columns": [
                        {"name": "claim_id", "type": "string", "nullable": False},
                        {"name": "candidate_id", "type": "string", "nullable": False},
                        {"name": "content_hash", "type": "string", "nullable": False},
                        {"name": "gate_receipt_id", "type": "string", "nullable": False},
                        {"name": "exec_locus", "type": "string", "nullable": False},
                        {"name": "authority", "type": "string", "nullable": False},
                        {"name": "scope_label", "type": "string", "nullable": False},
                        {"name": "migration_name", "type": "string", "nullable": True},
                        {"name": "database", "type": "string", "nullable": True},
                        {"name": "model_id", "type": "string", "nullable": True},
                        {"name": "provider", "type": "string", "nullable": True},
                        {"name": "domain", "type": "string", "nullable": True},
                        {"name": "promoted_at", "type": "timestamp", "nullable": False},
                        {"name": "detail", "type": "string", "nullable": True},
                    ],
                    "partition_columns": ["exec_locus"],
                    "properties": {
                        "delta.enableDeletionVectors": "false",
                        "write_authority": "bcs_gate_only",
                        "readers": "all",
                    },
                },
                {
                    "name": "routing_decision_ledger",
                    "comment": "Committed routing decisions promoted from agents_home",
                    "columns": [
                        {"name": "decision_id", "type": "string", "nullable": False},
                        {"name": "request_id", "type": "string", "nullable": False},
                        {"name": "exec_locus", "type": "string", "nullable": False},
                        {"name": "fallback_tier", "type": "integer", "nullable": False},
                        {"name": "model_team", "type": "string", "nullable": True},
                        {"name": "resolved_provider", "type": "string", "nullable": True},
                        {"name": "cloud_shift_active", "type": "boolean", "nullable": False},
                        {"name": "outcome", "type": "string", "nullable": False},
                        {"name": "decided_at", "type": "timestamp", "nullable": False},
                    ],
                    "partition_columns": ["exec_locus"],
                    "properties": {"write_authority": "bcs_gate_only"},
                },
            ],
        },
        {
            "name": "provider_home_ledger",
            "comment": "Candidate promotion history from provider_home (read-only projection)",
            "tables": [
                {
                    "name": "promotion_history",
                    "comment": "Record of all model candidates promoted through the BCS gate",
                    "columns": [
                        {"name": "promotion_id", "type": "string", "nullable": False},
                        {"name": "candidate_id", "type": "string", "nullable": False},
                        {"name": "content_hash", "type": "string", "nullable": False},
                        {"name": "gate_receipt_id", "type": "string", "nullable": False},
                        {"name": "model_id", "type": "string", "nullable": False},
                        {"name": "provider", "type": "string", "nullable": False},
                        {"name": "domain", "type": "string", "nullable": False},
                        {"name": "exec_locus", "type": "string", "nullable": False},
                        {"name": "benchmark_score", "type": "double", "nullable": True},
                        {"name": "cost_per_1k_tokens", "type": "double", "nullable": True},
                        {"name": "latency_p50_ms", "type": "integer", "nullable": True},
                        {"name": "promoted_at", "type": "timestamp", "nullable": False},
                        {"name": "source", "type": "string", "nullable": True},
                    ],
                    "partition_columns": ["domain", "exec_locus"],
                    "properties": {"write_authority": "bcs_gate_only"},
                }
            ],
        },
    ],
}


def _schema_content_hash() -> str:
    import hashlib
    content = json.dumps(UNITY_SCHEMA_V001, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()


class UnityMigratorClient:
    """Thin wrapper around the Unity Catalog REST API.

    Only the BCS gate managed identity has write access. This client
    presents the claim to the BCS gate, which performs the actual write.
    Direct REST calls here are for schema verification (read-only) only.
    """

    def __init__(self, endpoint: str, token: str):
        self.endpoint = endpoint.rstrip("/")
        self.token = token

    def _headers(self) -> dict:
        return {
            "Authorization": "Bearer %s" % self.token,
            "Content-Type": "application/json",
        }

    def schema_exists(self, catalog: str, schema_name: str) -> bool:
        import urllib.request
        import urllib.error
        url = "%s/api/2.1/unity-catalog/schemas/%s.%s" % (self.endpoint, catalog, schema_name)
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=15):
                return True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False
            raise

    def catalog_exists(self, catalog: str) -> bool:
        import urllib.request
        import urllib.error
        url = "%s/api/2.1/unity-catalog/catalogs/%s" % (self.endpoint, catalog)
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=15):
                return True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False
            raise


def run_unity_migration(catalog: str, dry_run: bool = False) -> CompletionClaim:
    endpoint = os.environ.get("UNITY_CATALOG_ENDPOINT", "")
    token = os.environ.get("UNITY_CATALOG_TOKEN", "")
    bcs_gate_url = os.environ.get("BCS_GATE_URL", "")

    if not endpoint and not dry_run:
        raise RuntimeError(
            "UNITY_CATALOG_ENDPOINT is not set. "
            "Set it to the Managed Unity Catalog REST endpoint for the MyDude Azure workspace."
        )

    claim = CompletionClaim(
        exec_locus="in_azure",
        authority=MigrationAuthority.UNITY,
        scope_label=ScopeLabel.V7_SCOPE_LABEL,
        migration_name="unity_V001_initial_ledger_schema",
        database="unity_catalog",
        detail={
            "catalog": catalog,
            "schema_version": UNITY_SCHEMA_V001["version"],
            "description": UNITY_SCHEMA_V001["description"],
        },
    )
    claim.content_hash = _schema_content_hash()

    # V1-V7 scope gates
    gate = ScopeGate(claim, expected_authority=MigrationAuthority.UNITY)
    passed = gate.run_all()
    logger.info("All scope gates passed for Unity migration: %s", passed)

    if dry_run:
        logger.info("[DRY RUN] Would submit Unity schema claim. candidate_id=%s", claim.candidate_id)
        print(json.dumps(claim.to_dict(), indent=2))
        return claim

    # Step 1: Bootstrap Unity Catalog schema/tables via the BCS gate's /bootstrap/unity.
    # The BCS gate is the sole writer — it creates catalog, schemas, and tables idempotently.
    # This must complete before the first claim row insert.
    if bcs_gate_url:
        import urllib.request
        import urllib.error
        bootstrap_url = bcs_gate_url + "/bootstrap/unity"
        logger.info("Bootstrapping Unity Catalog schema via BCS gate: %s", bootstrap_url)
        req = urllib.request.Request(bootstrap_url, data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                bs_result = json.loads(resp.read().decode())
                logger.info("Unity schema bootstrap: %s", bs_result)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            raise RuntimeError("Unity schema bootstrap failed (HTTP %d): %s" % (e.code, body))
        except urllib.error.URLError as e:
            raise RuntimeError("BCS gate unreachable for Unity bootstrap: %s" % e)
    else:
        logger.warning("BCS_GATE_URL not set; skipping Unity schema bootstrap (safe only in dry-run or local mode).")

    # Step 2: Verify catalog exists (read-only probe — belt-and-suspenders after bootstrap)
    if endpoint and token:
        client = UnityMigratorClient(endpoint, token)
        if not client.catalog_exists(catalog):
            raise RuntimeError(
                "Unity Catalog '%s' does not exist at endpoint '%s' even after bootstrap. "
                "Check BCS gate logs for bootstrap errors." % (catalog, endpoint)
            )
        logger.info("Unity Catalog '%s' verified to exist.", catalog)

    # Step 3: Submit the CompletionClaim to the BCS gate — it commits the schema migration record.
    result = submit_completion_claim(claim, bcs_gate_url=bcs_gate_url)
    logger.info("Unity CompletionClaim submitted: %s", result)
    return claim


def main():
    parser = argparse.ArgumentParser(description="MyDude Unity/Iceberg Migrator")
    parser.add_argument("--catalog", default="mydude", help="Unity Catalog name (default: mydude)")
    parser.add_argument("--dry-run", action="store_true", help="Validate without applying changes")
    args = parser.parse_args()

    try:
        claim = run_unity_migration(args.catalog, dry_run=args.dry_run)
        print("OK  unity_catalog=%s  candidate_id=%s  receipt=%s"
              % (args.catalog, claim.candidate_id, claim.gate_receipt_id))
    except Exception as e:
        logger.error("Unity migration failed: %s", e)
        print("FAIL  unity  %s" % e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
