#!/usr/bin/env python3
"""MyDude Knowledge-Corpus Migrator.

Registers the knowledge-corpus manifest with the governance authority model:
the governance ledger of record is Postgres (governance.claim_ledger), and the
knowledge corpus itself is staged to Microsoft Fabric / OneLake (ADLS Gen2).
Each change is submitted through the BCS scope-completion gate (V1-V7).

AUTHORITY BOUNDARY: This migrator never touches Postgres DDL.
All emitted CompletionClaims have authority=MigrationAuthority.FABRIC.
The Postgres migrator's claims have authority=POSTGRES — they are
separate migration lineages that must never cross.

The BCS gate (single Entra managed identity) is the sole truth writer: it
records the claim in the Postgres governance ledger and stages the corpus
manifest to OneLake/ADLS. This migrator only presents the claim.

Usage:
    python corpus_migrator.py --corpus mydude
    python corpus_migrator.py --corpus mydude --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from base import (
    CompletionClaim,
    MigrationAuthority,
    ScopeGate,
    ScopeLabel,
    submit_completion_claim,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("corpus_migrator")

# ---------------------------------------------------------------------------
# Knowledge-corpus manifest definition
# Each manifest is versioned; content_hash is derived from the spec.
# The corpus is staged to OneLake/ADLS; the governance ledger of record is
# Postgres (governance.claim_ledger), written by the BCS gate.
# ---------------------------------------------------------------------------
CORPUS_MANIFEST_V001 = {
    "corpus": "mydude",
    "version": "V001",
    "description": "Initial MyDude knowledge-corpus manifest (Fabric/OneLake staging)",
    "staging": {
        "platform": "fabric_onelake",
        "backend": "adls_gen2",
        "filesystem": "onelake-staging",
        "format": "lancedb",
    },
    "collections": [
        {
            "name": "claim_corpus",
            "comment": "Knowledge-corpus projection of all BCS-promoted claims",
            "fields": [
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
            "partition_fields": ["exec_locus"],
            "properties": {
                "write_authority": "bcs_gate_only",
                "readers": "all",
            },
        },
        {
            "name": "routing_decision_corpus",
            "comment": "Committed routing decisions projected from agents_home",
            "fields": [
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
            "partition_fields": ["exec_locus"],
            "properties": {"write_authority": "bcs_gate_only"},
        },
        {
            "name": "promotion_history_corpus",
            "comment": "Record of all model candidates promoted through the BCS gate",
            "fields": [
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
            "partition_fields": ["domain", "exec_locus"],
            "properties": {"write_authority": "bcs_gate_only"},
        },
    ],
}


def _manifest_content_hash() -> str:
    import hashlib
    content = json.dumps(CORPUS_MANIFEST_V001, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()


def run_corpus_migration(corpus: str, dry_run: bool = False) -> CompletionClaim:
    bcs_gate_url = os.environ.get("BCS_GATE_URL", "")

    if not bcs_gate_url and not dry_run:
        raise RuntimeError(
            "BCS_GATE_URL is not set. "
            "Set it to the BCS promotion-gate endpoint for the MyDude Azure deployment. "
            "The gate is the sole writer to the governance ledger and the OneLake corpus staging."
        )

    claim = CompletionClaim(
        exec_locus="in_azure",
        authority=MigrationAuthority.FABRIC,
        scope_label=ScopeLabel.V7_SCOPE_LABEL,
        migration_name="corpus_V001_initial_knowledge_corpus",
        database="fabric_onelake",
        detail={
            "corpus": corpus,
            "manifest_version": CORPUS_MANIFEST_V001["version"],
            "description": CORPUS_MANIFEST_V001["description"],
            "staging": CORPUS_MANIFEST_V001["staging"],
        },
    )
    claim.content_hash = _manifest_content_hash()

    # V1-V7 scope gates
    gate = ScopeGate(claim, expected_authority=MigrationAuthority.FABRIC)
    passed = gate.run_all()
    logger.info("All scope gates passed for corpus migration: %s", passed)

    if dry_run:
        logger.info("[DRY RUN] Would submit knowledge-corpus claim. candidate_id=%s", claim.candidate_id)
        print(json.dumps(claim.to_dict(), indent=2))
        return claim

    # Submit the CompletionClaim to the BCS gate. The gate records the migration
    # in the Postgres governance ledger (governance.claim_ledger) and stages the
    # corpus manifest to OneLake/ADLS. The migrator never writes either directly.
    result = submit_completion_claim(claim, bcs_gate_url=bcs_gate_url)
    logger.info("Knowledge-corpus CompletionClaim submitted: %s", result)
    return claim


def main():
    parser = argparse.ArgumentParser(description="MyDude Knowledge-Corpus Migrator")
    parser.add_argument("--corpus", default="mydude", help="Knowledge-corpus name (default: mydude)")
    parser.add_argument("--dry-run", action="store_true", help="Validate without applying changes")
    args = parser.parse_args()

    try:
        claim = run_corpus_migration(args.corpus, dry_run=args.dry_run)
        print("OK  corpus=%s  candidate_id=%s  receipt=%s"
              % (args.corpus, claim.candidate_id, claim.gate_receipt_id))
    except Exception as e:
        logger.error("Corpus migration failed: %s", e)
        print("FAIL  corpus  %s" % e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
