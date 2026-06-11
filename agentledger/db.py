"""Isolated SQLite engine for the Agent Ledger.

Deliberately decoupled from `src/database.py` (which targets the app's
PostgreSQL). The ledger is agent infrastructure and must never share a
connection, schema, or lifecycle with user-facing application data.
"""
from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

LEDGER_DIR = os.path.dirname(os.path.abspath(__file__))
LEDGER_DB_PATH = os.path.join(LEDGER_DIR, "agent_ledger.db")
LEDGER_URL = os.environ.get("AGENT_LEDGER_URL", f"sqlite:///{LEDGER_DB_PATH}")

engine = create_engine(LEDGER_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()


def init_ledger(drop: bool = False) -> None:
    """Create (or recreate) the ledger schema.

    `drop=True` rebuilds from scratch — used by the seeder so the ledger always
    reflects current real project state without stale rows.
    """
    from agentledger import models  # noqa: F401  (register mappers)

    if drop:
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
