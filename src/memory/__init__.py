"""
src/memory — unified memory substrate for the MyDude.io governance stack.

Provides a storage-agnostic adapter interface that the swarm calls directly,
backed by the vendored Cognee (local KG) and Mem0 (cloud) engines, plus a
bidirectional sync bridge.

Public surface:
  get_substrate()      → MemorySubstrate   # singleton
  MemoryEntry          → dataclass
  MemoryEvent          → audit event dataclass
"""

from .substrate import get_substrate, MemorySubstrate
from .adapter import MemoryEntry, MemoryEvent

__all__ = ["get_substrate", "MemorySubstrate", "MemoryEntry", "MemoryEvent"]
