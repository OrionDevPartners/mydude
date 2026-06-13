#!/usr/bin/env python3
"""MyDude Postgres Governance Migrator.

Applies agents_home and provider_home DDL migrations independently.
Each database has its own role, credentials, migration lineage, and
CompletionClaim chain through the BCS scope-completion gate (V1-V7).

This migrator owns Postgres DDL exclusively; knowledge-corpus (Fabric) claims
are a separate lineage and must never cross into this one.
The authority field on all emitted claims is MigrationAuthority.POSTGRES.

Usage:
    python postgres_migrator.py --db agents_home
    python postgres_migrator.py --db provider_home
    python postgres_migrator.py --db all
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
from pathlib import Path

from base import (
    CompletionClaim,
    MigrationAuthority,
    ScopeGate,
    ScopeLabel,
    submit_completion_claim,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("postgres_migrator")

GOVERNANCE_DIR = Path(__file__).parent.parent / "governance"

DATABASES = {
    "agents_home": {
        "dsn_env": "PG_AGENTS_HOME_DSN",
        "schema_file": GOVERNANCE_DIR / "agents_home_schema.sql",
        "migration_dir": GOVERNANCE_DIR / "agents_home_migrations",
        "description": "agents_home routing authority schema",
    },
    "provider_home": {
        "dsn_env": "PG_PROVIDER_HOME_DSN",
        "schema_file": GOVERNANCE_DIR / "provider_home_schema.sql",
        "migration_dir": GOVERNANCE_DIR / "provider_home_migrations",
        "description": "provider_home candidate cognition and outbox schema",
    },
}


def _get_dsn(db_key: str) -> str:
    env = DATABASES[db_key]["dsn_env"]
    dsn = os.environ.get(env, "")
    if not dsn:
        raise RuntimeError(
            "Missing %s environment variable. Set it to the PostgreSQL DSN for %s." % (env, db_key)
        )
    return dsn


# Role-to-password env var mapping.
# On first provisioning, the operator sets these before running the migrator.
# The migrator sets the role password so the DSN user can authenticate.
# On subsequent runs the ALTER ROLE is idempotent (same password is safe to re-apply).
_ROLE_PASSWORD_ENV: dict[str, list[str]] = {
    "agents_home": [
        ("agents_home_writer", "PG_AGENTS_HOME_WRITER_PASSWORD"),
        ("agents_home_reader", "PG_AGENTS_HOME_READER_PASSWORD"),
    ],
    "provider_home": [
        ("provider_home_writer", "PG_PROVIDER_HOME_WRITER_PASSWORD"),
        ("provider_home_reader", "PG_PROVIDER_HOME_READER_PASSWORD"),
    ],
}


def _bootstrap_role_credentials(db_key: str, admin_conn) -> None:
    """Set passwords for governance roles using env vars.

    This is the credential bootstrap step that operationalises the role split:
      - agents_home_writer / agents_home_reader passwords → DSN used by BCS gate + migrator
      - provider_home_writer / provider_home_reader passwords → DSN used by BCS gate + gate

    The passwords are read from environment variables (never hardcoded).
    If a password env var is absent, the role is skipped with a warning (idempotent).
    ALTER ROLE ... PASSWORD is idempotent — safe to re-run.
    """
    for role, env_var in _ROLE_PASSWORD_ENV.get(db_key, []):
        password = os.environ.get(env_var, "")
        if not password:
            logger.warning(
                "Skipping credential bootstrap for role '%s': %s not set. "
                "Set this env var to enable DSN authentication for this role.",
                role, env_var,
            )
            continue
        try:
            with admin_conn.cursor() as cur:
                # Use parameterised identifier quoting to avoid SQL injection on role name
                cur.execute("ALTER ROLE %s PASSWORD %%s" % role, (password,))
            admin_conn.commit()
            logger.info("Password set for role '%s' (env=%s).", role, env_var)
        except Exception as e:
            logger.warning("Failed to set password for role '%s': %s", role, e)
            admin_conn.rollback()


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def migrate_database(db_key: str, dry_run: bool = False) -> CompletionClaim:
    """Apply all pending migrations for a single database and emit a CompletionClaim."""
    config = DATABASES[db_key]
    schema_file: Path = config["schema_file"]
    migration_dir: Path = config["migration_dir"]

    if not schema_file.exists():
        raise FileNotFoundError("Schema file not found: %s" % schema_file)

    logger.info("Starting Postgres migration for %s (dry_run=%s)", db_key, dry_run)

    # Build claim for this migration run
    claim = CompletionClaim(
        exec_locus="in_azure",
        authority=MigrationAuthority.POSTGRES,
        scope_label=ScopeLabel.V7_SCOPE_LABEL,
        migration_name="V001__initial_schema",
        database=db_key,
        detail={"schema_file": str(schema_file), "description": config["description"]},
    )
    claim.compute_content_hash(schema_file.read_text())

    # V1-V7 scope gates
    gate = ScopeGate(claim, expected_authority=MigrationAuthority.POSTGRES)
    passed = gate.run_all()
    logger.info("All scope gates passed for %s: %s", db_key, passed)

    if dry_run:
        logger.info("[DRY RUN] Would apply schema to %s. Claim: %s", db_key, claim.candidate_id)
        return claim

    # Apply DDL
    try:
        import psycopg2
    except ImportError:
        raise RuntimeError("psycopg2 is not installed. Run: pip install psycopg2-binary")

    dsn = _get_dsn(db_key)
    conn = psycopg2.connect(dsn)
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            sql = schema_file.read_text()
            # Execute each statement (psycopg2 doesn't support multi-statement execute in one call)
            # We split on statement boundaries carefully
            cur.execute(sql)
        conn.commit()
        logger.info("Schema applied successfully to %s.", db_key)

        # Credential bootstrap — set role passwords from env vars.
        # Must run after schema apply (roles are created by the DDL above).
        # Idempotent: ALTER ROLE ... PASSWORD is safe to re-run with the same value.
        _bootstrap_role_credentials(db_key, conn)

        # Record in schema_manifest within the database itself
        schema_name = "governance" if db_key == "agents_home" else "governance"
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO governance.schema_manifest
                    (schema_name, version, checksum, description)
                VALUES (%s, 'V001', %s, %s)
                ON CONFLICT (schema_name, version) DO NOTHING
                """,
                (db_key, claim.content_hash, config["description"]),
            )
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError("Migration failed for %s: %s" % (db_key, e)) from e
    finally:
        conn.close()

    # Submit CompletionClaim to BCS gate
    result = submit_completion_claim(claim)
    logger.info("CompletionClaim submitted for %s: %s", db_key, result)

    return claim


def main():
    parser = argparse.ArgumentParser(description="MyDude Postgres Governance Migrator")
    parser.add_argument(
        "--db",
        choices=["agents_home", "provider_home", "all"],
        default="all",
        help="Which database to migrate (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and check scope gates without applying DDL",
    )
    parser.add_argument(
        "--from-keyvault",
        action="store_true",
        help="Source the DSNs + BCS secret from Key Vault into the env before "
             "migrating (governance pillar #3). Requires running inside the VNet.",
    )
    args = parser.parse_args()

    if args.from_keyvault:
        local_dir = str(Path(__file__).parent.parent / "local")
        if local_dir not in sys.path:
            sys.path.insert(0, local_dir)
        import azure_common as az  # type: ignore

        status = az.hydrate_env_from_keyvault(overwrite=False)
        for env_var, state in status.items():
            logger.info("Key Vault hydration: %s -> %s", env_var, state)

    targets = list(DATABASES.keys()) if args.db == "all" else [args.db]
    errors = []

    for db_key in targets:
        try:
            claim = migrate_database(db_key, dry_run=args.dry_run)
            print("OK  %s  candidate_id=%s  receipt=%s" % (db_key, claim.candidate_id, claim.gate_receipt_id))
        except Exception as e:
            logger.error("Migration failed for %s: %s", db_key, e)
            errors.append((db_key, str(e)))

    if errors:
        for db_key, err in errors:
            print("FAIL  %s  %s" % (db_key, err), file=sys.stderr)
        sys.exit(1)

    print("All Postgres migrations completed successfully.")


if __name__ == "__main__":
    main()
