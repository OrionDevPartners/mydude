"""Database capability adapter — PostgreSQL via SQLAlchemy.

Wraps the existing ``src.database`` module (the project's live SQLAlchemy
engine and session factory) behind the unified CapabilityAdapter interface.
This is the first real adapter for the "database" capability category — no
stubs, no placeholders (Governance Pillar #1).

The DATABASE_URL secret is sourced entirely by the existing engine
initialization and the Replit Secrets layer; this adapter only probes that
the engine is reachable.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

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
            import os
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
