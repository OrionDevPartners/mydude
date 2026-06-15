"""Knowledge / graph store capability adapters.

Two real adapters for the "knowledge_store" category:

  * ``CogneeKnowledgeAdapter`` — wraps the existing ``src.memory.local_store``
    (Cognee-backed knowledge graph) behind the unified interface.

  * ``Mem0KnowledgeAdapter`` — wraps the existing ``src.memory.cloud_store``
    (Mem0-backed semantic memory) behind the unified interface.

Both delegate entirely to the existing substrate implementations — no new
vendor coupling, no placeholders (Governance Pillar #1 + #2).
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from src.capabilities.base import CapabilityAdapter, CapabilitySpec

logger = logging.getLogger(__name__)


class CogneeKnowledgeAdapter(CapabilityAdapter):
    """Local knowledge graph via the existing Cognee-backed LocalMemoryAdapter.

    Cognee is a vendored in-process dependency (no outbound API key required).
    Availability is gated on successful adapter initialization and a stats()
    probe to confirm the adapter is operational.
    """

    def _probe(self) -> bool:
        try:
            from src.memory.local_store import LocalMemoryAdapter
            adapter = LocalMemoryAdapter()
            # stats() is the cheapest non-mutating call on both adapters.
            _ = adapter.stats()
            return True
        except Exception as exc:
            logger.debug("cognee knowledge store probe failed: %s", exc)
            return False

    def health_probe(self) -> Dict[str, Any]:
        try:
            from src.memory.local_store import LocalMemoryAdapter
            adapter = LocalMemoryAdapter()
            stats = adapter.stats()
            return {
                "ok": True,
                "detail": "available (Cognee local KG, %d entries)" % stats.get("total", 0),
                "exec_locus": self.exec_locus,
            }
        except Exception as exc:
            return {
                "ok": False,
                "detail": "unavailable (Cognee not importable or init failed: %s)" % exc,
                "exec_locus": self.exec_locus,
            }


class Mem0KnowledgeAdapter(CapabilityAdapter):
    """Cloud semantic memory via the existing Mem0-backed CloudMemoryAdapter.

    Mem0 requires a network endpoint; availability is gated on the adapter
    importing cleanly and returning stats (which checks for the optional mem0
    package and any required credentials via the existing cloud_store module).
    """

    def _probe(self) -> bool:
        try:
            from src.memory.cloud_store import CloudMemoryAdapter
            adapter = CloudMemoryAdapter()
            _ = adapter.stats()
            return True
        except Exception as exc:
            logger.debug("mem0 knowledge store probe failed: %s", exc)
            return False

    def health_probe(self) -> Dict[str, Any]:
        try:
            from src.memory.cloud_store import CloudMemoryAdapter
            adapter = CloudMemoryAdapter()
            stats = adapter.stats()
            return {
                "ok": True,
                "detail": "available (Mem0 cloud memory, %d entries)" % stats.get("total", 0),
                "exec_locus": self.exec_locus,
            }
        except Exception as exc:
            return {
                "ok": False,
                "detail": "unavailable (mem0 not configured or package missing: %s)" % exc,
                "exec_locus": self.exec_locus,
            }
