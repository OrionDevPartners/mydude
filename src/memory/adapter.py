"""
Memory adapter interface — storage-agnostic dataclasses and the abstract base.

Both LocalMemoryAdapter (Cognee) and CloudMemoryAdapter (Mem0) implement
MemoryAdapterBase so the substrate can address either without importing
engine-specific types.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class MemoryEventType(str, Enum):
    RECALL = "recall"
    PERSIST = "persist"
    CONSOLIDATE = "consolidate"
    SYNC = "sync"
    DECAY = "decay"


@dataclass
class MemoryEntry:
    """A single memory item that moves through the substrate."""

    memory_id: str
    content: str
    category: str = "fact"
    confidence: float = 1.0
    source: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    access_count: int = 0
    decay: float = 1.0
    verified: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "memory_id": self.memory_id,
            "content": self.content,
            "category": self.category,
            "confidence": self.confidence,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "access_count": self.access_count,
            "decay": self.decay,
            "verified": self.verified,
            "metadata": self.metadata,
        }


@dataclass
class MemoryEvent:
    """Audit event emitted when memory is recalled, persisted, or synced."""
    event_type: MemoryEventType
    detail: str
    memory_ids: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_log_str(self) -> str:
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.timestamp))
        ids_str = ", ".join(self.memory_ids[:5])
        return f"[MEMORY:{self.event_type.value.upper()}] {ts} — {self.detail}" + (
            f" (ids: {ids_str})" if ids_str else ""
        )


class MemoryAdapterBase(ABC):
    """Abstract interface that both Cognee and Mem0 adapters implement."""

    @abstractmethod
    def add(self, entry: MemoryEntry) -> MemoryEntry:
        ...

    @abstractmethod
    def search(self, query: str, top_k: int = 5,
               category: Optional[str] = None) -> List[MemoryEntry]:
        ...

    @abstractmethod
    def get_all(self) -> List[MemoryEntry]:
        ...

    @abstractmethod
    def delete(self, memory_id: str) -> bool:
        ...

    @abstractmethod
    def apply_decay(self) -> None:
        ...

    @abstractmethod
    def stats(self) -> Dict:
        ...
