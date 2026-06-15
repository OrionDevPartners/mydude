"""Database capability adapters — relational backends.

Two real, operative backends prove the zero-code-change swap guarantee for the
``database`` capability category (Governance Pillar #1 — no placeholders):

  * :class:`PostgreSQLAdapter` — wraps the project's live SQLAlchemy engine
    (``src.database``), the Replit managed PostgreSQL primary.
  * :class:`SQLiteAdapter` — a self-contained file/in-memory SQLite backend
    using the stdlib ``sqlite3`` module. It needs no server and no secret, so
    it is always available and acts as the durable local fallback.

Swapping between them is a single ``config/providers.toml`` edit (change the
``[database].enabled`` order / costs) — no call-site changes. The resolver
picks the cheapest available backend, so PostgreSQL (cost 0) leads when
``DATABASE_URL`` is present and SQLite (cost 5) takes over otherwise.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Sequence

from src.capabilities.base import CapabilityAdapter, CapabilitySpec

logger = logging.getLogger(__name__)


class PostgreSQLAdapter(CapabilityAdapter):
    """Wraps the existing SQLAlchemy engine (src.database) as a governed
    capability adapter.

    Availability is gated on a cheap ``SELECT 1`` connectivity probe so the
    capability matrix accurately reflects live database reachability.
    """

    def secrets_present(self) -> bool:
        """The database URL is managed by the platform (Replit built-in DB);
        presence is confirmed by the engine being importable and a URL existing."""
        try:
            url = os.environ.get("DATABASE_URL", "")
            return bool(url)
        except Exception:
            return False

    def _probe(self) -> bool:
        """Execute a SELECT 1 against the live database engine.

        Uses the existing src.database.SessionLocal so the probe goes through
        the same connection pool that all application DB queries use.
        """
        try:
            from src.database import SessionLocal
            from sqlalchemy import text
            db = SessionLocal()
            try:
                db.execute(text("SELECT 1"))
                return True
            finally:
                db.close()
        except Exception as exc:
            logger.debug("database probe failed: %s", exc)
            return False

    def health_probe(self) -> Dict[str, Any]:
        ok = self._probe()
        return {
            "ok": ok,
            "detail": "connected (SELECT 1 OK)" if ok
                      else "unreachable (database engine probe failed)",
            "exec_locus": self.exec_locus,
        }


class SQLiteAdapter(CapabilityAdapter):
    """Self-contained SQLite relational backend (stdlib ``sqlite3``).

    A real, operative second database backend — no server, no secret. The
    database file path is sourced (in precedence order) from:

      1. the ``path`` key in this backend's config/providers.toml block
         (``[databasebackends.sqlite].path``),
      2. the ``SQLITE_DB_PATH`` environment variable,
      3. a sensible default under ``/tmp``.

    Use ``:memory:`` as the path for an ephemeral in-process database.
    Availability is gated on an actual ``SELECT 1`` against a live connection,
    so the capability matrix reflects whether the file is genuinely usable.
    """

    _DEFAULT_PATH = "/tmp/mydude_sqlite.db"

    @property
    def db_path(self) -> str:
        """Resolve the SQLite database file path from config then env then default."""
        configured = (self.spec.extra or {}).get("path")
        if configured:
            return str(configured)
        return os.environ.get("SQLITE_DB_PATH", self._DEFAULT_PATH)

    def connect(self):
        """Open a live ``sqlite3.Connection`` to the resolved path.

        Caller owns the connection lifecycle (use as a context manager or
        ``close()`` it). Raises on a genuine failure — never returns a stub.
        """
        import sqlite3
        path = self.db_path
        if path != ":memory:":
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        return sqlite3.connect(path)

    def execute(
        self,
        sql: str,
        params: Optional[Sequence[Any]] = None,
    ) -> List[tuple]:
        """Execute a statement and return all rows (operative, not a placeholder).

        Commits write statements so the backend is genuinely usable for
        durable storage, not merely a connectivity probe.
        """
        conn = self.connect()
        try:
            cur = conn.execute(sql, tuple(params or ()))
            rows = cur.fetchall()
            conn.commit()
            return rows
        finally:
            conn.close()

    def _probe(self) -> bool:
        """Open a connection and run ``SELECT 1`` to confirm real usability."""
        try:
            conn = self.connect()
            try:
                conn.execute("SELECT 1").fetchone()
                return True
            finally:
                conn.close()
        except Exception as exc:
            logger.debug("sqlite probe failed: %s", exc)
            return False

    def health_probe(self) -> Dict[str, Any]:
        ok = self._probe()
        location = ":memory:" if self.db_path == ":memory:" else self.db_path
        return {
            "ok": ok,
            "detail": ("connected (SELECT 1 OK @ %s)" % location) if ok
                      else ("unreachable (sqlite open/probe failed @ %s)" % location),
            "exec_locus": self.exec_locus,
        }
