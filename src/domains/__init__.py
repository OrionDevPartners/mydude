"""Logical domain containers for MyDude.io.

Each business domain (finance, coach, fleet, telephony, sales, avatar,
subscriptions, browser) plus the shared ``core`` runtime is described by a
``DomainContainer``. The container declares the domain's physical database key,
its semantic-search / vector namespace, its long-term-memory namespace and its
RAG configuration, so every subsystem (DB routing, memory substrate, vector
store) can resolve a single source of truth for "which domain owns this".

These are LOGICAL containers inside one FastAPI process — there is no Docker or
per-domain service. Isolation is enforced at the data layer: each domain gets its
own physical Postgres database and its own per-domain vector/memory namespace.
"""

from .registry import (
    DomainContainer,
    CORE_DOMAIN,
    DOMAIN_CONTAINERS,
    all_containers,
    get_container,
    resolve_domain,
    table_domain,
    domain_table_names,
    replicated_table_names,
)

__all__ = [
    "DomainContainer",
    "CORE_DOMAIN",
    "DOMAIN_CONTAINERS",
    "all_containers",
    "get_container",
    "resolve_domain",
    "table_domain",
    "domain_table_names",
    "replicated_table_names",
]
