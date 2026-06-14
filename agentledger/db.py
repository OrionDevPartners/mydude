"""Isolated SQLite engine for the Agent Ledger.

Deliberately decoupled from `src/database.py` (which targets the app's
PostgreSQL). The ledger is agent infrastructure and must never share a
connection, schema, or lifecycle with user-facing application data.
"""
from __future__ import annotations

import os
from typing import Iterable, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

LEDGER_DIR = os.path.dirname(os.path.abspath(__file__))
LEDGER_DB_PATH = os.path.join(LEDGER_DIR, "agent_ledger.db")
LEDGER_URL = os.environ.get("AGENT_LEDGER_URL", f"sqlite:///{LEDGER_DB_PATH}")

engine = create_engine(LEDGER_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()


def init_ledger(drop: bool = False, preserve: Optional[Iterable[str]] = None) -> None:
    """Create (or recreate) the ledger schema.

    `drop=True` rebuilds from scratch — used by the seeder so the ledger always
    reflects current real project state without stale rows.

    `preserve=[...]` names tables to KEEP (table + rows) across a drop. The seeder
    passes the append-only ``ledger_events`` table here so the rebuild audit log
    accumulates across merges instead of being wiped on every reseed. Any other
    table is dropped and recreated empty, so non-audit data is always rebuilt
    fresh from real project state.
    """
    from agentledger import models  # noqa: F401  (register mappers)

    if drop:
        preserve_names = set(preserve or ())
        if preserve_names:
            # drop_all reverses dependency order internally; restrict to the
            # tables we are NOT preserving. checkfirst=True (default) makes this
            # safe on a fresh DB where nothing exists yet.
            tables = [t for t in Base.metadata.sorted_tables
                      if t.name not in preserve_names]
            Base.metadata.drop_all(bind=engine, tables=tables)
        else:
            Base.metadata.drop_all(bind=engine)
    # create_all is checkfirst=True, so preserved tables are left untouched and
    # only the just-dropped (or missing) tables are recreated.
    Base.metadata.create_all(bind=engine)
