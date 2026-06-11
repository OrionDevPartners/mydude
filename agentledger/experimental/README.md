# Experimental Embedded Memory Stack (dev-only)

A serverless-first, embedded memory stack for Replit agents. It lives in this
**experimental container**: code that is *referenced but not deployed to
production*. A runtime gate refuses to initialize it inside a Replit deployment.

> Governance: this is **real, fully-functional code** — real connections, real
> schemas, smoke-tested CRUD. It is dev-only by *gating*, not by being a stub.

## The four stores

| Store             | Engine                          | Role                                            |
|-------------------|---------------------------------|-------------------------------------------------|
| `VectorStore`     | DuckDB (`FLOAT[]` + cosine)     | Semantic memory / code-chunk recall             |
| `GraphStore`      | DuckDB (+ `ATTACH` agent ledger)| AST / dependency graph, joinable to the ledger  |
| `LedgerStore`     | PostgreSQL (psycopg pool)       | Durable, append-only episodic event log         |
| `CheckpointStore` | PostgreSQL (psycopg pool)       | Durable run state / checkpoints                 |
| `WorkingMemory`   | SQLite (stdlib)                 | Fast ephemeral per-session scratchpad           |

The DuckDB graph store `ATTACH`es `agentledger/agent_ledger.db` **read-only**, so
graph queries can `JOIN` the agent ledger's structural facts (layers, containers,
857 functions, dependencies) with zero data duplication.

## The gate (`gate.py`)

- In a Replit deployment (`REPLIT_DEPLOYMENT == "1"`) initialization is **blocked**.
- In development it is **enabled** by default.
- `AGENT_MEMORY_STACK=1` force-enables (even in a deployment); `=0` force-disables.
- `MemoryManager(force=True)` bypasses the gate deliberately (explicit + auditable).

## Usage

```python
from agentledger.experimental import MemoryManager

with MemoryManager() as mem:
    # Semantic memory (uses the offline LocalHashingEmbedder by default)
    mem.vectors.add("def login(user): ...", source="auth.py")
    hits = mem.vectors.search("authentication", k=3)

    # AST / dependency graph (joinable with the ledger)
    mem.ast.add_node("auth.login", kind="function", path="auth.py")
    rows = mem.ast.query("SELECT count(*) FROM ledger.functions")  # if attached

    # Durable episodic ledger + checkpoints (Postgres, namespaced schema)
    mem.ledger.append("sess-1", "task.started", payload={"goal": "build X"})
    cid = mem.checkpoints.put("thread-1", {"step": 1})

    # Ephemeral working memory
    mem.working.set("sess-1", "draft", {"title": "WIP"})

    print(mem.health())
```

### Plugging in a real embedder (provider-agnostic)

The default `LocalHashingEmbedder` is a real, deterministic offline embedder so
the stack works with zero credentials. For production-grade semantics, inject any
`str -> Sequence[float]` callable and match `embedding_dim`:

```python
mem = MemoryManager(embedder=my_openai_embed_fn, embedding_dim=1536)
```

## Configuration

| Env var               | Default            | Meaning                                   |
|-----------------------|--------------------|-------------------------------------------|
| `AGENT_MEMORY_STACK`  | (unset)            | Force enable (`1`) / disable (`0`)        |
| `AGENT_MEMORY_DIR`    | `data/agent_memory`| Directory for the embedded DB files       |
| `DATABASE_URL`        | (Replit built-in)  | Postgres DSN for ledger + checkpoints      |

Postgres objects are created in a namespaced schema (`agent_memory`) so they never
collide with the application's `public` tables.

## Deliberate deviations from the original spec

- **DuckDB native vectors instead of LanceDB** — LanceDB has no wheel for newer
  Python in this resolver and pulls heavy native deps. DuckDB's
  `array_cosine_similarity` gives an embedded vector store in the engine we
  already use for the graph. Swap point: a LanceDB-backed `VectorStore` with the
  same `add`/`search` contract.
- **Plain psycopg durable store instead of `langgraph-checkpoint-postgres`** —
  WaveOrchestrator already owns the LangGraph-style orchestration role. The
  `CheckpointStore` schema (`thread_id` / `checkpoint_id` / `state`) is shaped so
  a LangGraph `PostgresSaver` can be dropped on later.

## Dependencies (dev-only)

Installed into the dev environment (not added to the production dependency
closure): `duckdb`, `psycopg[binary,pool]`. `sqlite3` is stdlib.

```bash
uv pip install duckdb "psycopg[binary,pool]"
```

## Smoke test

```bash
python -m agentledger.experimental.smoke_test
```

Exercises every store end-to-end against a temporary data dir and a disposable
Postgres schema, then verifies the production gate blocks initialization.
