"""DedupIndex — semantic + structural duplicate index for DevGuard.

Composes the existing DuckDB :class:`~agentledger.experimental.memory_manager.VectorStore`
(semantic recall via ``array_cosine_similarity``) and stores each code unit's
structural hashes in the row ``metadata`` so:

* an **exact** match (identical AST) is a metadata lookup,
* a **structural** match (renamed copy — same skeleton hash) is a metadata
  lookup, and
* a **semantic** near-duplicate is a cosine search (threshold 0.85).

No faiss, no second SQLAlchemy store — we reuse the embedded vector store we
already have (anti-redundancy). This module is **alert-only**: it never edits,
merges, or synthesizes code.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional, Sequence

from ..gate import require_enabled
from ..memory_manager import Embedder, VectorStore
from .embedders import build_embedder, embedder_id
from .extractor import (
    CodeUnit,
    extract_units_from_file,
    extract_units_from_source,
    iter_python_files,
)

logger = logging.getLogger(__name__)

_MATCH_RANK = {"exact": 3, "structural": 2, "semantic": 1}


def _repo_root() -> Path:
    # devguard/index.py -> devguard -> experimental -> agentledger -> repo root
    return Path(__file__).resolve().parents[3]


def _default_index_path() -> str:
    env = os.environ.get("DEVGUARD_INDEX_PATH")
    if env:
        return env
    data_dir = _repo_root() / ".devguard"
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir / "dedup.duckdb")


def _default_roots() -> list[Path]:
    root = _repo_root()
    return [root / "src", root / "agentledger"]


@dataclass
class DuplicateAlert:
    """One existing code unit that a checked snippet duplicates."""

    match_type: str  # "exact" | "structural" | "semantic"
    score: float
    qualname: str
    file_path: str
    lineno: int
    node_type: str
    snippet: str

    def to_dict(self) -> dict:
        return asdict(self)

    def __str__(self) -> str:
        loc = f"{self.file_path}:{self.lineno}"
        pct = f"{self.score:.0%}" if self.match_type == "semantic" else "exact"
        return (
            f"[{self.match_type.upper()} {pct}] {self.node_type} "
            f"{self.qualname}  ({loc})"
        )


def _snippet(content: str, max_lines: int = 3) -> str:
    lines = content.splitlines()
    head = "\n".join(lines[:max_lines])
    if len(lines) > max_lines:
        head += "\n    ..."
    return head


class DedupIndex:
    """Persistent dedup index over MyDude's own source tree."""

    def __init__(
        self,
        *,
        db_path: Optional[str] = None,
        embedder: Optional[Embedder] = None,
        dim: Optional[int] = None,
        roots: Optional[Sequence[str | Path]] = None,
        force: bool = False,
    ) -> None:
        if embedder is None or dim is None:
            embedder, dim = build_embedder(force=force)
        else:
            require_enabled(force=force)
        self.embedder = embedder
        self.dim = dim
        self.embedder_id = embedder_id(embedder)
        self.db_path = db_path or _default_index_path()
        self.roots = [Path(r) for r in (roots or _default_roots())]
        self.store = VectorStore(self.db_path, self.dim, self.embedder)
        self._con = None

    # -- lifecycle -------------------------------------------------------- #
    def connect(self) -> "DedupIndex":
        self.store.connect()
        self._con = self.store._con
        self._con.execute(
            "CREATE TABLE IF NOT EXISTS devguard_meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        row = self._con.execute(
            "SELECT value FROM devguard_meta WHERE key = 'embedder_id'"
        ).fetchone()
        if row is not None and row[0] != self.embedder_id:
            raise RuntimeError(
                f"DevGuard index at {self.db_path} was built with embedder "
                f"{row[0]!r}, but the active embedder is {self.embedder_id!r}. "
                "Rebuild with DedupIndex.build(reset=True)."
            )
        return self

    def close(self) -> None:
        self.store.close()
        self._con = None

    def count(self) -> int:
        if self._con is None:
            self.connect()
        return self.store.count()

    # -- build ------------------------------------------------------------ #
    def build(self, *, reset: bool = True) -> dict:
        """(Re)index all configured roots. Returns ``{files, units, errors}``."""
        if reset:
            self.close()
            for suffix in ("", ".wal"):
                try:
                    os.remove(self.db_path + suffix)
                except FileNotFoundError:
                    pass
        self.connect()

        stats = {"files": 0, "units": 0, "errors": 0}
        batch: list[dict] = []
        for root in self.roots:
            if not root.exists():
                continue
            for path in iter_python_files(root):
                stats["files"] += 1
                try:
                    units = extract_units_from_file(path)
                except (SyntaxError, UnicodeDecodeError) as exc:
                    stats["errors"] += 1
                    logger.warning("devguard: skipping %s (%s)", path, exc)
                    continue
                for unit in units:
                    batch.append(self._unit_item(unit))
                    stats["units"] += 1
                    if len(batch) >= 500:
                        self.store.add_many(batch)
                        batch = []
        if batch:
            self.store.add_many(batch)

        self._con.execute("DELETE FROM devguard_meta WHERE key = 'embedder_id'")
        self._con.execute(
            "INSERT INTO devguard_meta VALUES ('embedder_id', ?)", [self.embedder_id]
        )
        logger.info(
            "devguard: indexed %d units from %d files (%d skipped) into %s",
            stats["units"],
            stats["files"],
            stats["errors"],
            self.db_path,
        )
        return stats

    def reindex_file(self, path: str | Path) -> int:
        """Replace all indexed units for a single file (for the live watcher)."""
        if self._con is None:
            self.connect()
        path = str(Path(path).resolve())
        self._con.execute(
            "DELETE FROM code_chunks "
            "WHERE json_extract_string(metadata, '$.file_path') = ?",
            [path],
        )
        try:
            units = extract_units_from_file(path)
        except (SyntaxError, UnicodeDecodeError) as exc:
            logger.warning("devguard reindex: skipping %s (%s)", path, exc)
            return 0
        for unit in units:
            self._add_unit(unit)
        return len(units)

    def _unit_item(self, unit: CodeUnit) -> dict:
        file_path = (
            str(Path(unit.file_path).resolve())
            if unit.file_path != "<input>"
            else unit.file_path
        )
        metadata = {
            "key": unit.key,
            "name": unit.name,
            "qualname": unit.qualname,
            "node_type": unit.node_type,
            "file_path": file_path,
            "lineno": unit.lineno,
            "end_lineno": unit.end_lineno,
            "loc": unit.loc,
            "normalized_hash": unit.normalized_hash,
            "exact_hash": unit.exact_hash,
        }
        return {"content": unit.source, "source": unit.key, "metadata": metadata}

    def _add_unit(self, unit: CodeUnit) -> None:
        self.store.add_many([self._unit_item(unit)])

    # -- query ------------------------------------------------------------ #
    def check(
        self,
        source: str,
        *,
        k: int = 5,
        threshold: float = 0.85,
        exclude_key: Optional[str] = None,
    ) -> list[DuplicateAlert]:
        """Return duplicate alerts for ``source`` (a function/class or snippet)."""
        if self._con is None:
            self.connect()
        try:
            units = extract_units_from_source(source, "<input>")
        except SyntaxError:
            units = []

        found: dict[str, DuplicateAlert] = {}
        if units:
            for unit in units:
                self._check_unit(unit, found, k=k, threshold=threshold, exclude_key=exclude_key)
        else:
            self._semantic(source, found, k=k, threshold=threshold, exclude_key=exclude_key)

        return sorted(
            found.values(),
            key=lambda a: (_MATCH_RANK[a.match_type], a.score),
            reverse=True,
        )

    def _check_unit(
        self,
        unit: CodeUnit,
        found: dict,
        *,
        k: int,
        threshold: float,
        exclude_key: Optional[str],
    ) -> None:
        for content, meta in self._rows_by_meta("exact_hash", unit.exact_hash, exclude_key):
            self._offer(found, "exact", 1.0, meta, content)
        for content, meta in self._rows_by_meta(
            "normalized_hash", unit.normalized_hash, exclude_key
        ):
            self._offer(found, "structural", 1.0, meta, content)
        self._semantic(unit.source, found, k=k, threshold=threshold, exclude_key=exclude_key)

    def _semantic(
        self,
        text: str,
        found: dict,
        *,
        k: int,
        threshold: float,
        exclude_key: Optional[str],
    ) -> None:
        embedding = self.embedder(text)
        for row in self.store.search(text, k=k, embedding=embedding):
            if row["score"] < threshold:
                continue
            meta = row["metadata"]
            if exclude_key and meta.get("key") == exclude_key:
                continue
            self._offer(found, "semantic", float(row["score"]), meta, row["content"])

    def _rows_by_meta(
        self, field: str, value: str, exclude_key: Optional[str], limit: int = 25
    ) -> list[tuple[str, dict]]:
        # `field` is a fixed internal constant, never user input.
        sql = (
            "SELECT content, metadata FROM code_chunks "
            f"WHERE json_extract_string(metadata, '$.{field}') = ?"
        )
        params: list = [value]
        if exclude_key:
            sql += " AND json_extract_string(metadata, '$.key') != ?"
            params.append(exclude_key)
        sql += " LIMIT ?"
        params.append(limit)
        rows = self._con.execute(sql, params).fetchall()
        return [(r[0], json.loads(r[1]) if r[1] else {}) for r in rows]

    def _offer(self, found: dict, match_type: str, score: float, meta: dict, content: str) -> None:
        key = meta.get("key") or f"{meta.get('file_path')}::{meta.get('qualname')}"
        candidate = DuplicateAlert(
            match_type=match_type,
            score=score,
            qualname=meta.get("qualname", ""),
            file_path=meta.get("file_path", ""),
            lineno=int(meta.get("lineno", 0) or 0),
            node_type=meta.get("node_type", ""),
            snippet=_snippet(content),
        )
        existing = found.get(key)
        if existing is None or (_MATCH_RANK[match_type], score) > (
            _MATCH_RANK[existing.match_type],
            existing.score,
        ):
            found[key] = candidate
