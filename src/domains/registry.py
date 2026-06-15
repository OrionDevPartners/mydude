"""Domain container registry + model/table ownership map.

This module is intentionally free of any ``src.models`` import so it can be
imported very early (by ``src.database`` while it builds engines) without an
import cycle. Ownership is therefore keyed by ``__tablename__`` strings, which
are stable and match the SQLAlchemy table names in ``Base.metadata``.

Governance: provider-agnostic. The container only names *which* database/namespace
a domain uses (a logical key); the concrete connection string is resolved at
runtime by ``src.database`` (connector proxy / env / derived sibling DB), never
hardcoded here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

CORE_DOMAIN = "core"


@dataclass(frozen=True)
class DomainContainer:
    """Logical container describing one business domain's isolated resources."""

    slug: str
    #: logical database key (resolved to a real URL by src.database)
    db_key: str
    #: namespace for this domain's per-domain vector / semantic-search space
    vector_namespace: str
    #: namespace for this domain's long-term memory (Cognee dir + Mem0 agent id)
    memory_namespace: str
    #: jurisdiction slug this domain maps onto (src.swarm.jurisdiction)
    jurisdiction_slug: str = "general"
    #: per-domain RAG configuration knobs (recall depth, min score, etc.)
    rag_config: Dict[str, object] = field(default_factory=dict)
    #: set of __tablename__ values physically owned by this domain's database
    tables: Set[str] = field(default_factory=frozenset)
    #: human label
    label: str = ""


# --------------------------------------------------------------------------- #
# Table ownership — keyed by __tablename__ (verified against src/models.py)
# --------------------------------------------------------------------------- #
_FINANCE_TABLES = frozenset({
    "finance_projects", "finance_budgets", "finance_vendors",
    "finance_transactions", "plaid_items", "vendor_project_rules",
    "finance_sync_runs", "finance_write_requests", "finance_audit_logs",
})
_COACH_TABLES = frozenset({
    "mood_signals", "coach_insights", "secretary_requests", "coach_audit_logs",
})
_FLEET_TABLES = frozenset({
    "bot_teams", "bots", "provisioned_resources", "provisioning_jobs",
    "burst_workers", "burst_events",
})
_TELEPHONY_TABLES = frozenset({"call_sessions", "call_audio"})
_SALES_TABLES = frozenset({"sales_conversations"})
_AVATAR_TABLES = frozenset({"avatar_profiles", "avatar_sessions", "avatar_audit_logs"})
_SUBSCRIPTION_TABLES = frozenset({"subscriptions", "subscription_actions"})

#: Memory / vector tables replicated into EVERY domain database (incl. core) so
#: each domain owns its own isolated long-term memory + embedding space.
_REPLICATED_TABLES = frozenset({"memory_entries", "memory_audit_logs"})

#: Raw (non-ORM) pgvector table created in every domain database.
VECTOR_TABLE = "vector_entries"


def _container(slug, jurisdiction, tables, label) -> DomainContainer:
    return DomainContainer(
        slug=slug,
        db_key=slug,
        vector_namespace=f"vec::{slug}",
        memory_namespace=slug,
        jurisdiction_slug=jurisdiction,
        rag_config={"top_k": 5, "min_score": 0.0},
        tables=tables,
        label=label,
    )


DOMAIN_CONTAINERS: Dict[str, DomainContainer] = {
    CORE_DOMAIN: DomainContainer(
        slug=CORE_DOMAIN,
        db_key=CORE_DOMAIN,
        vector_namespace="vec::core",
        memory_namespace="core",
        jurisdiction_slug="general",
        rag_config={"top_k": 5, "min_score": 0.0},
        tables=frozenset(),  # core owns "everything else" (computed in src.database)
        label="Core runtime (auth, tasks, swarm, governance)",
    ),
    "finance": _container("finance", "finance", _FINANCE_TABLES, "Finance & bookkeeping"),
    "coach": _container("coach", "general", _COACH_TABLES, "Coach / digital twin"),
    "fleet": _container("fleet", "engineering", _FLEET_TABLES, "Fleet & provisioning"),
    "telephony": _container("telephony", "customer_service", _TELEPHONY_TABLES, "Telephony"),
    "sales": _container("sales", "marketing", _SALES_TABLES, "Sales"),
    "avatar": _container("avatar", "general", _AVATAR_TABLES, "Avatar / presence"),
    "subscriptions": _container(
        "subscriptions", "general", _SUBSCRIPTION_TABLES, "Subscriptions & billing"),
    # browser owns no tables of its own — it operates over core Task/Capability.
    "browser": _container("browser", "general", frozenset(), "Browser automation"),
}

#: union of every domain-owned table (everything NOT here lives in core)
DOMAIN_OWNED_TABLES: Set[str] = frozenset().union(
    *(c.tables for c in DOMAIN_CONTAINERS.values())
)

#: table_name -> owning domain slug (only domain-owned tables appear here)
_TABLE_TO_DOMAIN: Dict[str, str] = {}
for _c in DOMAIN_CONTAINERS.values():
    for _t in _c.tables:
        _TABLE_TO_DOMAIN[_t] = _c.slug


def all_containers() -> List[DomainContainer]:
    """Every domain container, core first."""
    ordered = [DOMAIN_CONTAINERS[CORE_DOMAIN]]
    ordered.extend(
        c for slug, c in DOMAIN_CONTAINERS.items() if slug != CORE_DOMAIN
    )
    return ordered


def replicated_table_names() -> Set[str]:
    """Memory/vector tables created in every domain database."""
    return set(_REPLICATED_TABLES)


def domain_table_names(slug: str) -> Set[str]:
    """ORM tables that physically live in *slug*'s database.

    Core owns everything that is not domain-owned (resolved by the caller against
    the live ``Base.metadata``); each business domain owns its declared tables
    plus the replicated memory tables.
    """
    container = DOMAIN_CONTAINERS.get(slug)
    if container is None:
        return set()
    if slug == CORE_DOMAIN:
        return set()  # caller computes "all - domain-owned" against metadata
    return set(container.tables) | set(_REPLICATED_TABLES)


def normalize_slug(domain: Optional[str]) -> str:
    """Sanitize a free-form domain string into a registry slug candidate."""
    if not domain:
        return CORE_DOMAIN
    s = re.sub(r"[^a-z0-9_]+", "_", str(domain).strip().lower()).strip("_")
    return s or CORE_DOMAIN


def resolve_domain(domain: Optional[str]) -> str:
    """Map any caller-supplied domain string to a known container slug.

    Unknown / general domains collapse to ``core`` so a stray domain label never
    silently creates a rogue database. ``general`` (the default jurisdiction) maps
    to core's shared store.
    """
    slug = normalize_slug(domain)
    if slug in ("", "general", "default", "shared"):
        return CORE_DOMAIN
    if slug in DOMAIN_CONTAINERS:
        return slug
    return CORE_DOMAIN


def get_container(domain: Optional[str]) -> DomainContainer:
    """Return the container for *domain* (resolved to a known slug)."""
    return DOMAIN_CONTAINERS[resolve_domain(domain)]


def table_domain(table_name: Optional[str]) -> str:
    """Owning domain slug for a table; core for shared/unknown tables."""
    if not table_name:
        return CORE_DOMAIN
    return _TABLE_TO_DOMAIN.get(table_name, CORE_DOMAIN)
