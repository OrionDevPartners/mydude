"""Experimental, development-only embedded memory stack for Replit agents.

This subpackage is **referenced but NOT deployed to production**. Importing it is
always safe; *initializing* :class:`MemoryManager` is blocked inside a Replit
deployment by the gate in :mod:`agentledger.experimental.gate` (override with the
``AGENT_MEMORY_STACK`` env var or ``force=True``).

The implementation is real and fully functional (governance pillar #1 — no
placeholders); it is simply inert in production by design.
"""

from .gate import (
    ProductionGuardError,
    is_enabled,
    is_production,
    require_enabled,
)
from .memory_manager import (
    CheckpointStore,
    GraphStore,
    LedgerStore,
    LocalHashingEmbedder,
    MemoryManager,
    VectorStore,
    WorkingMemory,
)

__all__ = [
    "MemoryManager",
    "LocalHashingEmbedder",
    "VectorStore",
    "GraphStore",
    "LedgerStore",
    "CheckpointStore",
    "WorkingMemory",
    "ProductionGuardError",
    "is_enabled",
    "is_production",
    "require_enabled",
]
