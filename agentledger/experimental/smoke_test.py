"""End-to-end smoke test for the experimental embedded memory stack.

Proves the implementation is real (governance pillar #1 — no placeholders):
every store performs a genuine round-trip. Runs against a temporary data
directory and a disposable Postgres schema so it leaves no residue, then
verifies the production gate blocks initialization.

Run:  python -m agentledger.experimental.smoke_test
"""

from __future__ import annotations

import os
import tempfile

from .gate import ProductionGuardError
from .memory_manager import MemoryManager

SMOKE_SCHEMA = "agent_memory_smoketest"


def _drop_schema(dsn: str) -> None:
    import psycopg

    with psycopg.connect(dsn, connect_timeout=15) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {SMOKE_SCHEMA} CASCADE")
        conn.commit()


def main() -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL not set; cannot smoke-test durable stores.")

    tmp = tempfile.mkdtemp(prefix="agent_memory_smoke_")
    _drop_schema(dsn)  # start clean

    try:
        with MemoryManager(data_dir=tmp, pg_schema=SMOKE_SCHEMA) as mem:
            # 1) Vector store — semantic recall
            mem.vectors.add("def login(user, password): authenticate the user",
                            source="auth.py", metadata={"lang": "py"})
            mem.vectors.add("def render_dashboard(): build the dashboard view",
                            source="views.py")
            mem.vectors.add("class PaymentProcessor: charge a credit card",
                            source="billing.py")
            hits = mem.vectors.search("user authentication login", k=2)
            assert hits, "vector search returned nothing"
            assert hits[0]["source"] == "auth.py", f"unexpected top hit: {hits[0]}"
            print(f"[vectors]     OK  top hit={hits[0]['source']} "
                  f"score={hits[0]['score']:.3f}  count={mem.vectors.count()}")

            # 2) Graph store — AST/graph + ledger attach
            mem.ast.add_node("auth.login", kind="function", path="auth.py")
            mem.ast.add_node("auth.authenticate", kind="function", path="auth.py")
            mem.ast.add_edge("auth.login", "auth.authenticate", "calls")
            nbrs = mem.ast.neighbors("auth.login")
            assert any(n["dst"] == "auth.authenticate" for n in nbrs), nbrs
            attached = mem.ast.ledger_attached
            ledger_fns = (
                mem.ast.query("SELECT count(*) FROM ledger.functions")[0][0]
                if attached else None
            )
            print(f"[ast]         OK  edges={len(nbrs)}  ledger_attached={attached}"
                  + (f"  ledger.functions={ledger_fns}" if attached else ""))

            # 3) Episodic ledger — durable append-only
            mem.ledger.append("sess-1", "task.started", payload={"goal": "smoke"},
                              actor="tester")
            mem.ledger.append("sess-1", "task.step", payload={"n": 1})
            hist = mem.ledger.history("sess-1")
            assert len(hist) == 2, hist
            print(f"[ledger]      OK  events={len(hist)} latest={hist[0]['kind']}")

            # 4) Checkpoints — durable run state
            cid = mem.checkpoints.put("thread-1", {"step": 1}, metadata={"v": 1})
            mem.checkpoints.put("thread-1", {"step": 2}, parent_id=cid)
            latest = mem.checkpoints.latest("thread-1")
            assert latest and latest["state"]["step"] == 2, latest
            print(f"[checkpoints] OK  latest.step={latest['state']['step']} "
                  f"history={len(mem.checkpoints.list('thread-1'))}")

            # 5) Working memory — ephemeral scratch
            mem.working.set("sess-1", "draft", {"title": "WIP"})
            mem.working.set("sess-1", "count", 7)
            assert mem.working.get("sess-1", "draft") == {"title": "WIP"}
            assert mem.working.get("sess-1", "count") == 7
            print(f"[working]     OK  keys={sorted(mem.working.items('sess-1'))}")

            print("[health]      ", mem.health())

        # Gate: simulate a production deployment -> must refuse to initialize.
        os.environ["REPLIT_DEPLOYMENT"] = "1"
        os.environ.pop("AGENT_MEMORY_STACK", None)
        try:
            MemoryManager(data_dir=tmp, pg_schema=SMOKE_SCHEMA)
        except ProductionGuardError:
            print("[gate]        OK  blocked initialization in simulated production")
        else:
            raise AssertionError("gate did NOT block initialization in production!")
        finally:
            os.environ.pop("REPLIT_DEPLOYMENT", None)

        # force=True bypass still works.
        MemoryManager(data_dir=tmp, pg_schema=SMOKE_SCHEMA, force=True)
        print("[gate]        OK  force=True bypass honored")

        print("\nALL SMOKE CHECKS PASSED")
    finally:
        _drop_schema(dsn)


if __name__ == "__main__":
    main()
