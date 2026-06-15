"""Idempotent one-pass data backfill for the per-domain database split (Task #260).

Before this migration every table lived in the single ``core`` database. After it,
each business domain owns a separate physical Postgres database (see
``src/domains/registry.py``). This script copies the pre-split rows that now belong
in a domain database OUT of the legacy core database and INTO the domain database:

  * Domain-owned business tables (e.g. ``call_sessions`` -> telephony DB): every
    row is copied verbatim.
  * Replicated memory tables (``memory_entries``, ``memory_audit_logs``): only the
    rows whose ``domain`` column matches the destination domain are copied; core
    rows stay in core.

It is safe to re-run: every insert uses ``ON CONFLICT DO NOTHING`` (keyed on the
primary key / unique constraints), and integer primary-key sequences are advanced
to ``MAX(id)`` after each copy so freshly inserted rows never collide with copied
ones. Nothing is deleted from the source — the legacy rows remain in core untouched
so a rollback is a no-op.

Governance: fail-loud (a copy error aborts that table and is reported in the
summary with a non-zero exit), provider-agnostic (uses the same routed engines as
the app), no placeholders.

Usage::

    python -m scripts.migrate_domain_dbs            # apply the backfill
    python -m scripts.migrate_domain_dbs --dry-run  # report what would be copied
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import List, Optional, Tuple

from sqlalchemy import inspect, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from src.database import Base, ensure_databases, get_engine, init_db
from src.domains.registry import (
    CORE_DOMAIN,
    all_containers,
    replicated_table_names,
)

logger = logging.getLogger("migrate_domain_dbs")

MEMORY_TABLES = frozenset(replicated_table_names())  # carry a ``domain`` column


def _shared_columns(src: Engine, dst: Engine, table: str) -> List[str]:
    """Columns present in BOTH the source and destination physical table.

    Reflecting both sides keeps the copy correct even if the legacy core table
    drifted (missing a newly added column, or carrying a dropped one).
    """
    src_cols = {c["name"] for c in inspect(src).get_columns(table)}
    dst_cols = {c["name"] for c in inspect(dst).get_columns(table)}
    ordered = [c.name for c in Base.metadata.tables[table].columns if c.name in src_cols and c.name in dst_cols]
    # Include any reflected columns not in the ORM metadata (defensive), stable-sorted.
    extra = sorted((src_cols & dst_cols) - set(ordered))
    return ordered + extra


def _pk_columns(table: str) -> List[str]:
    tbl = Base.metadata.tables.get(table)
    if tbl is None:
        return []
    return [c.name for c in tbl.primary_key.columns]


def _reset_sequence(dst: Engine, table: str) -> None:
    """Advance the integer PK sequence to MAX(id) so future inserts don't collide."""
    pk = _pk_columns(table)
    if len(pk) != 1:
        return
    col = pk[0]
    try:
        with dst.begin() as conn:
            seq = conn.execute(
                text("SELECT pg_get_serial_sequence(:t, :c)"), {"t": table, "c": col}
            ).scalar()
            if not seq:
                return
            conn.execute(
                text(
                    "SELECT setval(:s, (SELECT COALESCE(MAX(%s), 0) FROM %s), true)"
                    % (
                        dst.dialect.identifier_preparer.quote_identifier(col),
                        dst.dialect.identifier_preparer.quote_identifier(table),
                    )
                ),
                {"s": seq},
            )
    except Exception as exc:  # pragma: no cover - sequence reset is best-effort
        logger.warning("sequence reset skipped for %s.%s: %s", table, col, exc)


def _copy_table(
    src: Engine,
    dst: Engine,
    table: str,
    domain_filter: Optional[str],
    dry_run: bool,
) -> Tuple[int, int, str]:
    """Copy rows of *table* from *src* to *dst*.

    ``domain_filter`` (memory tables only) restricts the source rows to a single
    domain. Returns ``(source_rows, inserted_rows, note)``.
    """
    if not inspect(src).has_table(table):
        return (0, 0, "no source table")
    if not inspect(dst).has_table(table):
        return (0, 0, "no dest table")

    cols = _shared_columns(src, dst, table)
    if not cols:
        return (0, 0, "no shared columns")

    prep = dst.dialect.identifier_preparer
    qcols = ", ".join(prep.quote_identifier(c) for c in cols)
    where = ""
    params = {}
    if domain_filter is not None:
        where = " WHERE domain = :dom"
        params["dom"] = domain_filter

    with src.connect() as sconn:
        rows = sconn.execute(
            text("SELECT %s FROM %s%s" % (qcols, prep.quote_identifier(table), where)),
            params,
        ).mappings().all()

    if not rows:
        return (0, 0, "no rows")
    if dry_run:
        return (len(rows), 0, "dry-run")

    # Typed Core insert so column types (JSON/JSONB, arrays, enums) are adapted
    # correctly, with ON CONFLICT DO NOTHING for idempotent re-runs.
    dst_table = Base.metadata.tables[table]
    stmt = pg_insert(dst_table).on_conflict_do_nothing()
    payload = [{c: row.get(c) for c in cols} for row in rows]
    inserted = 0
    with dst.begin() as dconn:
        for record in payload:
            res = dconn.execute(stmt, record)
            inserted += res.rowcount if res.rowcount and res.rowcount > 0 else 0

    _reset_sequence(dst, table)
    return (len(rows), inserted, "ok")


def migrate(dry_run: bool = False) -> int:
    """Run the full backfill. Returns a process exit code (0 = success)."""
    # Make sure every domain DB + table subset exists before we copy into it.
    ensure_databases()
    init_db()

    core = get_engine(CORE_DOMAIN)
    failures = 0
    total_inserted = 0

    for container in all_containers():
        slug = container.slug
        if slug == CORE_DOMAIN:
            continue
        dst = get_engine(slug)

        # 1) domain-owned business tables — copy every row.
        for table in sorted(container.tables):
            try:
                src_n, ins_n, note = _copy_table(core, dst, table, None, dry_run)
                total_inserted += ins_n
                logger.info(
                    "[%s] %-26s source=%d inserted=%d (%s)", slug, table, src_n, ins_n, note
                )
            except Exception as exc:
                failures += 1
                logger.error("[%s] %s FAILED: %s", slug, table, exc)

        # 2) replicated memory tables — copy only rows tagged for this domain.
        for table in sorted(MEMORY_TABLES):
            try:
                src_n, ins_n, note = _copy_table(core, dst, table, slug, dry_run)
                total_inserted += ins_n
                logger.info(
                    "[%s] %-26s source=%d inserted=%d (%s)", slug, table, src_n, ins_n, note
                )
            except Exception as exc:
                failures += 1
                logger.error("[%s] %s FAILED: %s", slug, table, exc)

    logger.info(
        "Backfill %s: inserted=%d failures=%d",
        "DRY-RUN complete" if dry_run else "complete",
        total_inserted,
        failures,
    )
    return 1 if failures else 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill per-domain databases (Task #260).")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be copied without writing anything.",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return migrate(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
