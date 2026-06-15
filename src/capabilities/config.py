"""env_1 loader for ALL capability categories.

Reads config/providers.toml (the single committed provider-mapping layer) and
exposes per-category capability specs to the rest of the app. Contains no
secret values — only the NAMES of the secrets each provider/backend needs.

LLM and browser categories delegate to their existing config modules so no
behavior changes occur. New categories (database, vector_search, …) are read
from the same file using the same shape: [<category>] selection block +
[<category>backends.<key>] definition blocks.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from src.capabilities.base import CapabilitySpec
from src.providers.config import load_config

logger = logging.getLogger(__name__)

# All supported capability categories in canonical order.
ALL_CATEGORIES = [
    "llm",
    "browser",
    "database",
    "vector_search",
    "knowledge_store",
    "object_storage",
    "secrets_vault",
    "realtime",
    "orchestrator",
    "sig_optimizer",
    "container_compute",
]

# The TOML section that holds backend definitions for each category.
# Most use "<category>backends" to match the existing "browserbackends" pattern.
# LLM uses "providers" (legacy); browser uses "browserbackends" (legacy).
_BACKEND_TABLE: Dict[str, str] = {
    "llm": "providers",
    "browser": "browserbackends",
    "database": "databasebackends",
    "vector_search": "vectorbackends",
    "knowledge_store": "knowledgebackends",
    "object_storage": "storagebackends",
    "secrets_vault": "vaultbackends",
    "realtime": "realtimebackends",
    "orchestrator": "orchestratorbackends",
    "sig_optimizer": "optimizerbackends",
    "container_compute": "computebackends",
}


def _spec_from_entry(key: str, category: str, d: dict) -> CapabilitySpec:
    return CapabilitySpec(
        key=key,
        adapter=d.get("adapter", ""),
        category=category,
        secrets=list(d.get("secrets", [])),
        exec_locus=d.get("exec_locus", "local"),
        label=d.get("label", key),
        notes=d.get("notes", ""),
        cost=float(d.get("cost", 0) or 0),
        extra={
            k: v for k, v in d.items()
            if k not in ("adapter", "secrets", "exec_locus", "label", "notes", "cost")
        },
    )


def defined_specs_for(category: str) -> Dict[str, CapabilitySpec]:
    """All backend/provider specs defined for ``category``, keyed by key."""
    cfg = load_config()
    table = _BACKEND_TABLE.get(category, "%sbackends" % category)
    raw = cfg.get(table, {}) or {}
    return {k: _spec_from_entry(k, category, v) for k, v in raw.items()}


def category_enabled_keys(category: str) -> List[str]:
    """Keys listed in the [<category>].enabled list in env_1."""
    cfg = load_config()
    return list((cfg.get(category, {}) or {}).get("enabled", []))


def category_required_keys(category: str) -> List[str]:
    """Keys listed in the [<category>].required list in env_1."""
    cfg = load_config()
    return list((cfg.get(category, {}) or {}).get("required", []))


def ordered_specs_for(category: str) -> List[CapabilitySpec]:
    """Enabled specs for ``category``, sorted cheapest-cost first."""
    defined = defined_specs_for(category)
    enabled = [defined[k] for k in category_enabled_keys(category) if k in defined]
    return sorted(enabled, key=lambda s: s.cost)


def all_category_specs() -> Dict[str, List[CapabilitySpec]]:
    """Ordered specs for every known capability category."""
    return {cat: ordered_specs_for(cat) for cat in ALL_CATEGORIES}
