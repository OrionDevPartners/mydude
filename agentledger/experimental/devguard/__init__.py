"""DevGuard — unified, dev-gated semantic-dedup + lifecycle-guardian engine.

Consolidates the genuinely-new capabilities of three internal repos
(``ast-semantic-consolidator``, ``devgov``, ``ci-pr-bug-lifecycle-guardian``)
onto MyDude's existing infrastructure rather than duplicating it:

* semantic + structural code duplicate detection (the "alarm" so we never
  rebuild a capability that already exists), and
* a lifecycle "guardian" of pure-function safety gates (blast radius, repair
  confidence, repair authority) plus an append-only audit ledger.

Deliberately reused, not re-implemented (governance pillars — anti-redundancy):

* :class:`agentledger.experimental.memory_manager.VectorStore` (DuckDB cosine) —
  not faiss / a second SQLAlchemy store.
* :func:`agentledger.experimental.gate.require_enabled` — the single prod gate.
* :class:`agentledger.experimental.memory_manager.LedgerStore` (Postgres) — not
  a JSONL/SQLite side-ledger.
* ``src/selfheal`` circuit breaker — not a bespoke quarantine breaker.
* ``src/swarm`` governance — not devgov's overlapping guardrails/invariants.

Importing this package is always safe; *initializing* the engine routes through
:func:`agentledger.experimental.gate.require_enabled`, so it can never silently
ride into a production deployment (pillars #1 / #4).
"""

from __future__ import annotations

__all__ = ["build_embedder", "FastEmbedEmbedder"]


def __getattr__(name: str):
    """Lazily re-export public API so ``import devguard`` stays side-effect free."""
    if name in __all__:
        from . import embedders

        return getattr(embedders, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
