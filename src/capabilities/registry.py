"""Unified capability adapter registry.

Maps ``(category, adapter_name)`` pairs to concrete ``CapabilityAdapter``
subclasses. Registering a new backend requires only:
  1. Implementing the adapter class in ``src/capabilities/adapters/<category>.py``
  2. Adding it here
  3. Adding a ``[<category>backends.<key>]`` block in config/providers.toml

No call-site changes; the resolver selects adapters from this registry.
"""
from __future__ import annotations

from typing import Dict, Tuple, Type

from src.capabilities.base import CapabilityAdapter, CapabilitySpec

# ---------------------------------------------------------------------------
# Import all concrete adapter classes
# ---------------------------------------------------------------------------
from src.capabilities.adapters.llm import LLMCapabilityAdapter
from src.capabilities.adapters.browser import BrowserCapabilityAdapter
from src.capabilities.adapters.database import PostgreSQLAdapter
from src.capabilities.adapters.vector_search import (
    EmbeddingVectorAdapter,
    TFIDFVectorAdapter,
)
from src.capabilities.adapters.knowledge_store import (
    CogneeKnowledgeAdapter,
    Mem0KnowledgeAdapter,
)
from src.capabilities.adapters.object_storage import (
    LocalFSStorageAdapter,
    DBStorageAdapter,
    MemoryStorageAdapter,
)
from src.capabilities.adapters.secrets_vault import (
    ConnectorProxyVaultAdapter,
    EnvVaultAdapter,
)
from src.capabilities.adapters.realtime import TwilioRealtimeAdapter
from src.capabilities.adapters.orchestrator import WaveOrchestratorAdapter
from src.capabilities.adapters.sig_optimizer import DSPyOptimizerAdapter
from src.capabilities.adapters.container_compute import SubprocessComputeAdapter


class UnknownCapabilityAdapterError(RuntimeError):
    """Raised when env_1 references an adapter that is not registered."""


# ---------------------------------------------------------------------------
# The registry: (category, adapter_name) → concrete class
# ---------------------------------------------------------------------------
CAPABILITY_REGISTRY: Dict[Tuple[str, str], Type[CapabilityAdapter]] = {
    # LLM — delegates to the existing multi-provider swarm stack
    ("llm", "openai_chat"):          LLMCapabilityAdapter,
    ("llm", "anthropic_messages"):   LLMCapabilityAdapter,
    ("llm", "gemini_generate"):      LLMCapabilityAdapter,
    ("llm", "ollama_chat"):          LLMCapabilityAdapter,
    ("llm", "mlx_chat"):             LLMCapabilityAdapter,

    # Browser — delegates to the existing browser engine stack
    ("browser", "local_playwright"): BrowserCapabilityAdapter,
    ("browser", "browserbase"):      BrowserCapabilityAdapter,
    ("browser", "apify"):            BrowserCapabilityAdapter,
    ("browser", "agentcore"):        BrowserCapabilityAdapter,
    ("browser", "azure"):            BrowserCapabilityAdapter,

    # Database
    ("database", "postgresql"):      PostgreSQLAdapter,

    # Vector search
    ("vector_search", "embedding"):  EmbeddingVectorAdapter,
    ("vector_search", "tfidf"):      TFIDFVectorAdapter,

    # Knowledge / graph store
    ("knowledge_store", "cognee"):   CogneeKnowledgeAdapter,
    ("knowledge_store", "mem0"):     Mem0KnowledgeAdapter,

    # Object storage
    ("object_storage", "local_fs"):  LocalFSStorageAdapter,
    ("object_storage", "db_store"):  DBStorageAdapter,
    ("object_storage", "memory"):    MemoryStorageAdapter,

    # Secrets vault
    ("secrets_vault", "connector_proxy"): ConnectorProxyVaultAdapter,
    ("secrets_vault", "env_vault"):       EnvVaultAdapter,

    # Realtime / telephony
    ("realtime", "twilio"):          TwilioRealtimeAdapter,

    # Orchestrator
    ("orchestrator", "wave_orchestrator"): WaveOrchestratorAdapter,

    # Signature / prompt optimizer
    ("sig_optimizer", "dspy_bridge"): DSPyOptimizerAdapter,

    # Container / subprocess compute
    ("container_compute", "subprocess_local"): SubprocessComputeAdapter,
}


def build_adapter(spec: CapabilitySpec) -> CapabilityAdapter:
    """Instantiate the concrete adapter class for a given spec.

    Raises ``UnknownCapabilityAdapterError`` when env_1 references an adapter
    that has no registered class — a hard config error surfaced at boot.
    """
    cls = CAPABILITY_REGISTRY.get((spec.category, spec.adapter))
    if cls is None:
        registered = sorted(
            "%s/%s" % (cat, adp) for (cat, adp) in CAPABILITY_REGISTRY
        )
        raise UnknownCapabilityAdapterError(
            "Unknown adapter '%s' for category '%s'. "
            "Registered (category/adapter): %s"
            % (spec.adapter, spec.category, ", ".join(registered))
        )
    return cls(spec)


def registered_adapters_for(category: str) -> list:
    """Return the sorted list of registered adapter names for ``category``."""
    return sorted(adp for (cat, adp) in CAPABILITY_REGISTRY if cat == category)
