"""Serverless-first embedded memory stack for Replit agents (experimental).

Four real, fully-functional stores wired through one :class:`MemoryManager`:

==================  =========================  ==================================
Store               Engine                     Role
==================  =========================  ==================================
VectorStore         DuckDB (FLOAT[] + cosine)  Semantic memory / code-chunk recall
GraphStore          DuckDB (+ ATTACH ledger)   AST / dependency graph, joinable to
                                               the agent ledger with zero copy
LedgerStore         PostgreSQL (psycopg pool)  Durable, append-only episodic log
CheckpointStore     PostgreSQL (psycopg pool)  Durable run state / checkpoints
WorkingMemory       SQLite (stdlib)            Fast ephemeral per-session scratch
==================  =========================  ==================================

Deviations from the original spec (deliberate, governed):

* **DuckDB native vector search instead of LanceDB.** LanceDB has no wheel for
  newer Python in this resolver and drags heavy native deps. DuckDB's built-in
  ``array_cosine_similarity`` over ``FLOAT[N]`` columns gives a real, embedded
  vector store in the same engine we already use for the AST graph. Swap-in
  point: replace :class:`VectorStore` with a LanceDB-backed class implementing
  the same ``add``/``search`` contract.
* **Plain psycopg durable store instead of langgraph-checkpoint-postgres.**
  WaveOrchestrator already owns the LangGraph-style orchestration role, so we
  avoid the redundant dependency. :class:`CheckpointStore` uses a
  thread_id/checkpoint_id/state schema that a LangGraph ``PostgresSaver`` can be
  dropped onto later if desired.

Everything here is real (governance pillar #1 — no placeholders): connections,
schemas, and CRUD are operative and smoke-tested. The stack is gated to
development (see :mod:`agentledger.experimental.gate`).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from .gate import require_enabled

Embedder = Callable[[str], Sequence[float]]

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")
_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def _check_ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return name


# --------------------------------------------------------------------------- #
# Embedding seam (provider-agnostic — governance pillar #2)
# --------------------------------------------------------------------------- #
class LocalHashingEmbedder:
    """Deterministic, offline feature-hashing embedder.

    This is a *real* embedding algorithm (signed feature hashing with L2
    normalization), not a stub: cosine similarity of two outputs reflects token
    overlap. It makes the stack fully operative offline with zero credentials.

    For production-grade semantics, inject any callable ``str -> Sequence[float]``
    (e.g. an OpenAI/Gemini embedding function) as ``MemoryManager(embedder=...)``
    and set ``embedding_dim`` to that model's dimension.
    """

    def __init__(self, dim: int = 256) -> None:
        if dim <= 0:
            raise ValueError("dim must be positive")
        self.dim = dim

    def __call__(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in _TOKEN_RE.findall(text.lower()):
            h = int(hashlib.blake2b(tok.encode("utf-8"), digest_size=8).hexdigest(), 16)
            idx = h % self.dim
            sign = 1.0 if (h >> 1) & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


# --------------------------------------------------------------------------- #
# 1) Vector store — semantic memory (DuckDB)
# --------------------------------------------------------------------------- #
class VectorStore:
    """Embedded vector store over a DuckDB ``FLOAT[dim]`` column."""

    def __init__(self, path: str, dim: int, embedder: Optional[Embedder] = None) -> None:
        self.path = path
        self.dim = dim
        self.embedder = embedder
        self._con = None

    def connect(self) -> "VectorStore":
        import duckdb

        self._con = duckdb.connect(self.path)
        self._con.execute("CREATE SEQUENCE IF NOT EXISTS code_chunks_seq START 1")
        self._con.execute(
            f"""
            CREATE TABLE IF NOT EXISTS code_chunks (
                id          BIGINT PRIMARY KEY,
                content     TEXT NOT NULL,
                source      TEXT,
                metadata    JSON,
                embedding   FLOAT[{self.dim}] NOT NULL,
                created_at  TIMESTAMP DEFAULT now()
            )
            """
        )
        return self

    def _vec(self, text: str, embedding: Optional[Sequence[float]]) -> list[float]:
        if embedding is not None:
            v = [float(x) for x in embedding]
        elif self.embedder is not None:
            v = [float(x) for x in self.embedder(text)]
        else:
            raise RuntimeError(
                "no embedding supplied and no embedder configured for VectorStore"
            )
        if len(v) != self.dim:
            raise ValueError(f"embedding dim {len(v)} != store dim {self.dim}")
        return v

    def add(
        self,
        content: str,
        *,
        source: Optional[str] = None,
        metadata: Optional[dict] = None,
        embedding: Optional[Sequence[float]] = None,
    ) -> int:
        vec = self._vec(content, embedding)
        rid = self._con.execute("SELECT nextval('code_chunks_seq')").fetchone()[0]
        self._con.execute(
            "INSERT INTO code_chunks (id, content, source, metadata, embedding) "
            "VALUES (?, ?, ?, ?, ?)",
            [rid, content, source, json.dumps(metadata or {}), vec],
        )
        return int(rid)

    def add_many(self, items: "list[dict]") -> int:
        """Bulk-insert rows fast via Arrow (DuckDB array inserts are ~0.5s/row
        one at a time; an Arrow batch is ~1000x faster).

        Each item is a dict with ``content`` and optional ``source``,
        ``metadata``, ``embedding``. Embeddings missing from an item are computed
        via the configured embedder. Returns the number of rows inserted.
        """
        contents: list = []
        sources: list = []
        metas: list = []
        embs: list = []
        for it in items:
            content = it["content"]
            vec = self._vec(content, it.get("embedding"))
            contents.append(content)
            sources.append(it.get("source"))
            metas.append(json.dumps(it.get("metadata") or {}))
            embs.append(vec)
        if not contents:
            return 0

        try:
            import pyarrow as pa
        except ImportError:
            # Correct but slow fallback (~0.5s/row). Fail loud, not silent.
            import logging

            logging.getLogger(__name__).warning(
                "pyarrow not installed; VectorStore.add_many is using the slow "
                "row-by-row path for %d rows. Install pyarrow for bulk speed.",
                len(contents),
            )
            for content, source, meta, vec in zip(contents, sources, metas, embs):
                rid = self._con.execute(
                    "SELECT nextval('code_chunks_seq')"
                ).fetchone()[0]
                self._con.execute(
                    "INSERT INTO code_chunks (id, content, source, metadata, embedding) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [rid, content, source, meta, vec],
                )
            return len(contents)

        table = pa.table(
            {
                "content": pa.array(contents, pa.string()),
                "source": pa.array(sources, pa.string()),
                "metadata": pa.array(metas, pa.string()),
                "embedding": pa.array(embs, pa.list_(pa.float32(), self.dim)),
            }
        )
        self._con.register("_vs_bulk", table)
        try:
            self._con.execute(
                "INSERT INTO code_chunks (id, content, source, metadata, embedding) "
                "SELECT nextval('code_chunks_seq'), content, source, "
                "CAST(metadata AS JSON), embedding FROM _vs_bulk"
            )
        finally:
            self._con.unregister("_vs_bulk")
        return len(contents)

    def search(
        self,
        query: str,
        k: int = 5,
        *,
        embedding: Optional[Sequence[float]] = None,
    ) -> list[dict[str, Any]]:
        vec = self._vec(query, embedding)
        rows = self._con.execute(
            f"""
            SELECT id, content, source, metadata,
                   array_cosine_similarity(embedding, ?::FLOAT[{self.dim}]) AS score
            FROM code_chunks
            ORDER BY score DESC
            LIMIT ?
            """,
            [vec, k],
        ).fetchall()
        return [
            {
                "id": r[0],
                "content": r[1],
                "source": r[2],
                "metadata": json.loads(r[3]) if r[3] else {},
                "score": float(r[4]),
            }
            for r in rows
        ]

    def count(self) -> int:
        return int(self._con.execute("SELECT count(*) FROM code_chunks").fetchone()[0])

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None


# --------------------------------------------------------------------------- #
# 2) Graph store — AST / dependency graph (DuckDB, attaches the ledger)
# --------------------------------------------------------------------------- #
class GraphStore:
    """Relational AST/dependency graph; optionally ATTACHes the SQLite ledger
    read-only so graph rows can JOIN structural facts with zero data copy."""

    def __init__(self, path: str, ledger_db: Optional[str] = None) -> None:
        self.path = path
        self.ledger_db = ledger_db
        self.ledger_attached = False
        self._con = None

    def connect(self) -> "GraphStore":
        import duckdb

        self._con = duckdb.connect(self.path)
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS nodes (
                id    TEXT PRIMARY KEY,
                kind  TEXT NOT NULL,
                name  TEXT,
                path  TEXT,
                attrs JSON
            )
            """
        )
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS edges (
                src   TEXT NOT NULL,
                dst   TEXT NOT NULL,
                rel   TEXT NOT NULL,
                attrs JSON,
                PRIMARY KEY (src, dst, rel)
            )
            """
        )
        if self.ledger_db and os.path.exists(self.ledger_db):
            try:
                self._con.execute("INSTALL sqlite; LOAD sqlite;")
                self._con.execute(
                    f"ATTACH '{self.ledger_db}' AS ledger (TYPE sqlite, READ_ONLY)"
                )
                self.ledger_attached = True
            except Exception:
                # Degraded but honest: graph still works without the ledger join.
                self.ledger_attached = False
        return self

    def add_node(
        self,
        node_id: str,
        kind: str,
        *,
        name: Optional[str] = None,
        path: Optional[str] = None,
        attrs: Optional[dict] = None,
    ) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO nodes (id, kind, name, path, attrs) "
            "VALUES (?, ?, ?, ?, ?)",
            [node_id, kind, name, path, json.dumps(attrs or {})],
        )

    def add_edge(
        self, src: str, dst: str, rel: str, *, attrs: Optional[dict] = None
    ) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO edges (src, dst, rel, attrs) VALUES (?, ?, ?, ?)",
            [src, dst, rel, json.dumps(attrs or {})],
        )

    def neighbors(self, node_id: str, rel: Optional[str] = None) -> list[dict[str, Any]]:
        if rel is None:
            rows = self._con.execute(
                "SELECT src, dst, rel FROM edges WHERE src = ? OR dst = ?",
                [node_id, node_id],
            ).fetchall()
        else:
            rows = self._con.execute(
                "SELECT src, dst, rel FROM edges WHERE (src = ? OR dst = ?) AND rel = ?",
                [node_id, node_id, rel],
            ).fetchall()
        return [{"src": r[0], "dst": r[1], "rel": r[2]} for r in rows]

    def query(self, sql: str, params: Optional[Sequence[Any]] = None) -> list[tuple]:
        """Run arbitrary read SQL (e.g. JOIN nodes/edges with ``ledger.*``)."""
        return self._con.execute(sql, list(params or [])).fetchall()

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None


# --------------------------------------------------------------------------- #
# 3) Episodic ledger — durable append-only log (PostgreSQL)
# --------------------------------------------------------------------------- #
class LedgerStore:
    """Append-only episodic event log in a namespaced Postgres schema."""

    def __init__(self, pool, schema: str = "agent_memory") -> None:
        self.pool = pool
        self.schema = _check_ident(schema)

    def ensure_schema(self) -> None:
        with self.pool.connection() as conn:
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.schema}.episodic_ledger (
                    id         BIGSERIAL PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
                    kind       TEXT NOT NULL,
                    actor      TEXT,
                    payload    JSONB NOT NULL DEFAULT '{{}}'::jsonb
                )
                """
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS ix_episodic_session "
                f"ON {self.schema}.episodic_ledger (session_id, ts)"
            )

    def append(
        self,
        session_id: str,
        kind: str,
        *,
        payload: Optional[dict] = None,
        actor: Optional[str] = None,
    ) -> int:
        from psycopg.types.json import Json

        with self.pool.connection() as conn:
            row = conn.execute(
                f"INSERT INTO {self.schema}.episodic_ledger "
                f"(session_id, kind, actor, payload) VALUES (%s, %s, %s, %s) "
                f"RETURNING id",
                [session_id, kind, actor, Json(payload or {})],
            ).fetchone()
            return int(row[0])

    def history(self, session_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"SELECT id, ts, kind, actor, payload "
                f"FROM {self.schema}.episodic_ledger "
                f"WHERE session_id = %s ORDER BY ts DESC, id DESC LIMIT %s",
                [session_id, limit],
            ).fetchall()
        return [
            {"id": r[0], "ts": r[1], "kind": r[2], "actor": r[3], "payload": r[4]}
            for r in rows
        ]


# --------------------------------------------------------------------------- #
# 4) Checkpoints — durable run state (PostgreSQL, LangGraph-compatible shape)
# --------------------------------------------------------------------------- #
class CheckpointStore:
    """Durable checkpoint store keyed by ``(thread_id, checkpoint_id)``."""

    def __init__(self, pool, schema: str = "agent_memory") -> None:
        self.pool = pool
        self.schema = _check_ident(schema)

    def ensure_schema(self) -> None:
        with self.pool.connection() as conn:
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.schema}.checkpoints (
                    thread_id     TEXT NOT NULL,
                    checkpoint_id TEXT NOT NULL,
                    parent_id     TEXT,
                    state         JSONB NOT NULL,
                    metadata      JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (thread_id, checkpoint_id)
                )
                """
            )

    def put(
        self,
        thread_id: str,
        state: dict,
        *,
        checkpoint_id: Optional[str] = None,
        parent_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> str:
        from psycopg.types.json import Json

        cid = checkpoint_id or uuid.uuid4().hex
        with self.pool.connection() as conn:
            conn.execute(
                f"""
                INSERT INTO {self.schema}.checkpoints
                    (thread_id, checkpoint_id, parent_id, state, metadata)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (thread_id, checkpoint_id) DO UPDATE
                    SET state = EXCLUDED.state, metadata = EXCLUDED.metadata
                """,
                [thread_id, cid, parent_id, Json(state), Json(metadata or {})],
            )
        return cid

    def latest(self, thread_id: str) -> Optional[dict[str, Any]]:
        with self.pool.connection() as conn:
            row = conn.execute(
                f"SELECT thread_id, checkpoint_id, parent_id, state, metadata, created_at "
                f"FROM {self.schema}.checkpoints WHERE thread_id = %s "
                f"ORDER BY created_at DESC LIMIT 1",
                [thread_id],
            ).fetchone()
        if not row:
            return None
        return {
            "thread_id": row[0],
            "checkpoint_id": row[1],
            "parent_id": row[2],
            "state": row[3],
            "metadata": row[4],
            "created_at": row[5],
        }

    def list(self, thread_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"SELECT checkpoint_id, parent_id, created_at "
                f"FROM {self.schema}.checkpoints WHERE thread_id = %s "
                f"ORDER BY created_at DESC LIMIT %s",
                [thread_id, limit],
            ).fetchall()
        return [
            {"checkpoint_id": r[0], "parent_id": r[1], "created_at": r[2]} for r in rows
        ]


# --------------------------------------------------------------------------- #
# 5) Working memory — ephemeral per-session scratch (SQLite, stdlib)
# --------------------------------------------------------------------------- #
class WorkingMemory:
    """Fast key/value scratchpad scoped by ``session_id`` (SQLite)."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._con = None

    def connect(self) -> "WorkingMemory":
        import sqlite3

        self._con = sqlite3.connect(self.path)
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS scratch (
                session_id TEXT NOT NULL,
                key        TEXT NOT NULL,
                value      TEXT,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (session_id, key)
            )
            """
        )
        self._con.commit()
        return self

    def set(self, session_id: str, key: str, value: Any) -> None:
        self._con.execute(
            "INSERT INTO scratch (session_id, key, value, updated_at) "
            "VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT (session_id, key) DO UPDATE "
            "SET value = excluded.value, updated_at = datetime('now')",
            [session_id, key, json.dumps(value)],
        )
        self._con.commit()

    def get(self, session_id: str, key: str, default: Any = None) -> Any:
        row = self._con.execute(
            "SELECT value FROM scratch WHERE session_id = ? AND key = ?",
            [session_id, key],
        ).fetchone()
        return json.loads(row[0]) if row and row[0] is not None else default

    def items(self, session_id: str) -> dict[str, Any]:
        rows = self._con.execute(
            "SELECT key, value FROM scratch WHERE session_id = ?", [session_id]
        ).fetchall()
        return {k: (json.loads(v) if v is not None else None) for k, v in rows}

    def delete(self, session_id: str, key: str) -> None:
        self._con.execute(
            "DELETE FROM scratch WHERE session_id = ? AND key = ?", [session_id, key]
        )
        self._con.commit()

    def clear(self, session_id: str) -> None:
        self._con.execute("DELETE FROM scratch WHERE session_id = ?", [session_id])
        self._con.commit()

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
class MemoryManager:
    """Wires the four embedded stores behind one gated entrypoint.

    Use as a context manager::

        with MemoryManager() as mem:
            mem.vectors.add("def login(): ...", source="auth.py")
            hits = mem.vectors.search("authentication")
            mem.ledger.append("sess-1", "task.started", payload={"goal": "..."})
    """

    def __init__(
        self,
        *,
        data_dir: Optional[str] = None,
        embedding_dim: int = 256,
        embedder: Optional[Embedder] = None,
        pg_dsn: Optional[str] = None,
        pg_schema: str = "agent_memory",
        ledger_db: Optional[str] = None,
        force: bool = False,
    ) -> None:
        # GATE: refuse to initialize in production unless explicitly forced.
        require_enabled(force=force)

        self.data_dir = Path(
            data_dir or os.environ.get("AGENT_MEMORY_DIR", "data/agent_memory")
        )
        self.embedding_dim = embedding_dim
        self.embedder = embedder or LocalHashingEmbedder(embedding_dim)
        self.pg_dsn = pg_dsn or os.environ.get("DATABASE_URL")
        self.pg_schema = _check_ident(pg_schema)
        self.ledger_db = ledger_db or "agentledger/agent_ledger.db"

        self._pool = None
        self.vectors: Optional[VectorStore] = None
        self.ast: Optional[GraphStore] = None
        self.ledger: Optional[LedgerStore] = None
        self.checkpoints: Optional[CheckpointStore] = None
        self.working: Optional[WorkingMemory] = None
        self._connected = False

    def connect(self) -> "MemoryManager":
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Embedded engines (no network, no credentials).
        self.vectors = VectorStore(
            str(self.data_dir / "vectors.duckdb"), self.embedding_dim, self.embedder
        ).connect()
        self.ast = GraphStore(
            str(self.data_dir / "ast_graph.duckdb"), self.ledger_db
        ).connect()
        self.working = WorkingMemory(
            str(self.data_dir / "working_memory.db")
        ).connect()

        # Durable Postgres stores. Fail loud if no DSN (governance #1).
        if not self.pg_dsn:
            raise RuntimeError(
                "DATABASE_URL is not set; the durable ledger/checkpoint stores "
                "require PostgreSQL."
            )
        from psycopg_pool import ConnectionPool

        self._pool = ConnectionPool(
            conninfo=self.pg_dsn,
            min_size=1,
            max_size=4,
            open=True,
            kwargs={"connect_timeout": 15},
        )
        self.ledger = LedgerStore(self._pool, self.pg_schema)
        self.ledger.ensure_schema()
        self.checkpoints = CheckpointStore(self._pool, self.pg_schema)
        self.checkpoints.ensure_schema()

        self._connected = True
        return self

    def health(self) -> dict[str, Any]:
        """Real, live status of each store (counts / pings)."""
        if not self._connected:
            return {"connected": False}
        out: dict[str, Any] = {"connected": True, "data_dir": str(self.data_dir)}
        out["vectors"] = {"chunks": self.vectors.count()}
        out["ast"] = {"ledger_attached": self.ast.ledger_attached}
        with self._pool.connection() as conn:
            ep = conn.execute(
                f"SELECT count(*) FROM {self.pg_schema}.episodic_ledger"
            ).fetchone()[0]
            ck = conn.execute(
                f"SELECT count(*) FROM {self.pg_schema}.checkpoints"
            ).fetchone()[0]
        out["ledger"] = {"events": int(ep), "schema": self.pg_schema}
        out["checkpoints"] = {"rows": int(ck)}
        return out

    def close(self) -> None:
        for store in (self.vectors, self.ast, self.working):
            if store is not None:
                store.close()
        if self._pool is not None:
            self._pool.close()
            self._pool = None
        self._connected = False

    def __enter__(self) -> "MemoryManager":
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()
