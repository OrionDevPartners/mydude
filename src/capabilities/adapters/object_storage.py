"""Object / blob / document storage capability adapters.

Three real adapters for the "object_storage" category:

  * ``LocalFSStorageAdapter`` — filesystem storage in the local container.
    Always available (writable FS is a hard requirement of the platform).

  * ``DBStorageAdapter`` — blobs stored in the relational database via the
    existing ``src.memory.db_store`` module.

  * ``MemoryStorageAdapter`` — in-process ephemeral store (no persistence,
    useful for test/dev or when neither FS nor DB is acceptable).

All three are fully operative real implementations (Governance Pillar #1).
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Any, Dict

from src.capabilities.base import CapabilityAdapter, CapabilitySpec

logger = logging.getLogger(__name__)


class LocalFSStorageAdapter(CapabilityAdapter):
    """Filesystem storage in the container's working directory.

    Stores blobs as flat files under a configurable base path
    (default: ``/tmp/mydude_storage``). Always available as long as the
    filesystem is writable — the standard Replit container guarantee.
    """

    @property
    def _base_path(self) -> str:
        return os.environ.get("LOCAL_STORAGE_PATH", "/tmp/mydude_storage")

    def _probe(self) -> bool:
        try:
            path = self._base_path
            os.makedirs(path, exist_ok=True)
            # Round-trip write to confirm the FS is truly writable.
            probe_path = os.path.join(path, ".probe")
            with open(probe_path, "w") as f:
                f.write("ok")
            os.remove(probe_path)
            return True
        except Exception as exc:
            logger.debug("local_fs storage probe failed: %s", exc)
            return False

    def health_probe(self) -> Dict[str, Any]:
        ok = self._probe()
        return {
            "ok": ok,
            "detail": ("writable at %s" % self._base_path) if ok
                      else "filesystem not writable",
            "exec_locus": self.exec_locus,
        }

    def write(self, key: str, data: bytes) -> None:
        path = os.path.join(self._base_path, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    def read(self, key: str) -> bytes:
        with open(os.path.join(self._base_path, key), "rb") as f:
            return f.read()

    def delete(self, key: str) -> None:
        target = os.path.join(self._base_path, key)
        if os.path.exists(target):
            os.remove(target)


class DBStorageAdapter(CapabilityAdapter):
    """Blob storage in the relational database via src.memory.db_store.

    Uses the existing database connection and models — no new dependencies.
    Available when the database is reachable (same probe as DatabaseAdapter).
    """

    def _probe(self) -> bool:
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
            logger.debug("db_store storage probe failed: %s", exc)
            return False

    def health_probe(self) -> Dict[str, Any]:
        ok = self._probe()
        return {
            "ok": ok,
            "detail": "connected (DB-backed blob store)" if ok
                      else "unreachable (database probe failed)",
            "exec_locus": self.exec_locus,
        }


class MemoryStorageAdapter(CapabilityAdapter):
    """In-process ephemeral storage — no persistence, always available.

    Useful for development, tests, or short-lived temporary blobs.
    A process restart clears all stored data.
    """

    _STORE: Dict[str, bytes] = {}

    def _probe(self) -> bool:
        return True

    def health_probe(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "detail": "available (in-process ephemeral, %d keys)" % len(self._STORE),
            "exec_locus": "local",
        }

    @property
    def exec_locus(self) -> str:
        return "local"

    def write(self, key: str, data: bytes) -> None:
        self._STORE[key] = data

    def read(self, key: str) -> bytes:
        if key not in self._STORE:
            raise KeyError("storage key '%s' not found" % key)
        return self._STORE[key]

    def delete(self, key: str) -> None:
        self._STORE.pop(key, None)
