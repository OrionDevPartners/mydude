"""DevGuard capability guard — the dedup alarm at capability-request time.

When an agent asks the :class:`~src.swarm.broker.CapabilityBroker` for a
*new* (unimplemented) capability, this runs DevGuard's dedup alarm so we never
rebuild a capability the codebase already provides. Matches are reported to the
console, the JSONL audit trail, and the in-app Governance Center
(``SentinelEvent``).

It honours MyDude's governance pillars:

* **dev-gated** — a no-op in production. The cheap :func:`gate.is_enabled`
  check runs FIRST, before any heavy import (DuckDB / fastembed), so a prod
  deployment never pays the cost.
* **alert-only** — it never blocks, mutates, merges, or auto-implements
  anything; it only reports what already exists.
* **best-effort** — reporting must never break the broker's request path, so
  every failure is swallowed (logged only).

Two complementary checks (Option A — registry + semantic, no pseudo-units in
the vector store):

1. a fast normalized-name match against the authoritative capability registry
   (``capability_contracts.all_contracts()``) plus the broker's implemented
   handlers — an exact name hit means the capability already exists; and
2. a semantic :func:`scanner.check_duplicate` over the real codebase index,
   using only credential-safe descriptor text, to catch existing code that
   already does the same thing under a different name.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..gate import ProductionGuardError, is_enabled

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .index import DuplicateAlert

logger = logging.getLogger(__name__)

# Serializes index access so concurrent requests can't double-build the cached
# singleton (the scanner singleton is not internally locked).
_LOCK = threading.Lock()

# Cached known-capability inventory: normalized name -> human description.
_CAP_REGISTRY: Optional[Dict[str, str]] = None

# Cached fan-out sink (console + JSONL + in-app Governance Center).
_SINK: Any = None

# Capabilities implemented directly by the broker that may not declare a
# contract. Kept in sync with src/swarm/broker.py's handler branches; a miss
# here only downgrades an exact match to a semantic one (still alert-only).
_KNOWN_HANDLERS = {
    "git_status", "terraform_plan", "terraform_apply", "asana_query",
    "op_read_scoped", "browser_open", "browser_login", "browser_cancel",
    "ssh_run", "ssh_read_history", "ssh_fetch_code", "imap_read_receipts",
    "gmail_fetch_code", "bot_spawn", "fleet_provision_plan",
    "fleet_provision_approve",
}


def _normalize(name: Any) -> str:
    """Lower-case and collapse non-alphanumerics to single underscores."""
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


def index_capabilities(*, refresh: bool = False) -> Dict[str, str]:
    """Build (and cache) the known-capability inventory: name -> description.

    This is the dedup alarm's fast path for *named* capabilities: an exact
    normalized-name hit means the capability already exists, so no vector
    search is needed. Sourced from the authoritative, provider-agnostic
    capability contract registry plus the broker's implemented handlers.
    """
    global _CAP_REGISTRY
    if _CAP_REGISTRY is not None and not refresh:
        return _CAP_REGISTRY

    registry: Dict[str, str] = {}
    try:
        from src.swarm.capability_contracts import all_contracts

        for contract in all_contracts():
            registry[_normalize(contract.capability)] = (
                contract.description or contract.capability
            )
    except Exception:  # noqa: BLE001 - registry is best-effort
        logger.exception("devguard: failed to load capability contracts")

    for name in _KNOWN_HANDLERS:
        registry.setdefault(_normalize(name), name.replace("_", " "))

    _CAP_REGISTRY = registry
    return registry


def _descriptor(
    capability: str,
    params: Dict[str, Any],
    source: Optional[str],
    registry_desc: Optional[str],
) -> str:
    """Build credential-safe descriptor text for the semantic check.

    Only descriptive, non-secret fields are included — never raw ``url`` /
    ``command`` / credential params, which can carry tokens.
    """
    parts: List[str] = [str(capability).replace("_", " ")]
    if registry_desc:
        parts.append(registry_desc)
    for key in ("description", "intent", "purpose", "source"):
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    if source:
        parts.append(str(source))
    return ". ".join(parts)


def _sink() -> Any:
    """Lazily build and cache the fan-out sink (console + JSONL + Sentinel)."""
    global _SINK
    if _SINK is None:
        from .alerts import default_sink

        _SINK = default_sink(console=True, jsonl=True, sentinel=True)
    return _SINK


def on_new_capability(
    capability: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    source: Optional[str] = None,
    emit: bool = True,
) -> "List[DuplicateAlert]":
    """Run the dedup alarm for a newly-requested capability.

    Returns the list of duplicate alerts (possibly empty). When ``emit`` is
    true and any alerts are found, they are reported to the console, the JSONL
    audit trail, and the in-app Governance Center. Always safe to call: a
    disabled (production) environment returns ``[]`` immediately without
    importing any heavy dependency, and every failure is swallowed.
    """
    # Cheap gate pre-check BEFORE importing scanner/index (DuckDB + fastembed).
    try:
        if not is_enabled():
            return []
    except ValueError:
        # Malformed AGENT_MEMORY_STACK override: treat as disabled rather than
        # crashing the broker's request path.
        logger.warning("devguard: invalid AGENT_MEMORY_STACK; skipping dedup check")
        return []

    params = params or {}
    try:
        from .index import DuplicateAlert
        from .scanner import check_duplicate

        alerts: List[DuplicateAlert] = []
        seen: set = set()

        # 1) Registry fast path: is this capability name already known?
        registry = index_capabilities()
        norm = _normalize(capability)
        registry_desc = registry.get(norm)
        if registry_desc is not None:
            alerts.append(
                DuplicateAlert(
                    match_type="exact",
                    score=1.0,
                    qualname=capability,
                    file_path="src/swarm/capability_contracts.py",
                    lineno=0,
                    node_type="capability",
                    snippet=registry_desc,
                )
            )
            seen.add(("capability", norm))

        # 2) Semantic codebase check on credential-safe descriptor text.
        descriptor = _descriptor(capability, params, source, registry_desc)
        with _LOCK:
            matches = check_duplicate(descriptor)
        for match in matches:
            key = (match.file_path, match.qualname)
            if key in seen:
                continue
            seen.add(key)
            alerts.append(match)

        if emit and alerts:
            _sink().emit(alerts, source=f"capability:{norm}")
        return alerts
    except ProductionGuardError:
        # Gate flipped between the pre-check and the index build (e.g. another
        # thread). Honour it silently — DevGuard never runs in production.
        return []
    except Exception:  # noqa: BLE001 - reporting must never break the broker
        logger.exception("devguard: capability dedup check failed for %r", capability)
        return []
