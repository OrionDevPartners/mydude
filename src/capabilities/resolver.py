"""Unified capability resolver — the single entry point for all capability
resolution across every category.

Usage::

    from src.capabilities.resolver import get_resolver

    resolver = get_resolver()

    # Resolve the best available adapter for a category
    adapter = resolver.resolve("database")
    adapter = resolver.resolve("realtime")

    # Full capability matrix for the dashboard
    matrix = resolver.capability_matrix()

    # Force re-evaluation after a config or secret change
    resolver.reload()

Design notes
------------
* ``resolve(category)`` returns the first available, jurisdiction-permitted
  adapter for ``category`` ordered by cost (cheapest first), or raises
  ``CapabilityNotAvailable`` — never silently falls back to a no-op.
* LLM and browser categories delegate to their existing mature stacks with
  zero behavior change; new categories use this resolver directly.
* A short-lived availability cache avoids repeated TCP probes on the hot path.
* Thread-safe: resolver is a module-level singleton protected by a lock.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from src.capabilities.base import CapabilityAdapter, CapabilitySpec
from src.capabilities.config import (
    ALL_CATEGORIES,
    category_enabled_keys,
    category_required_keys,
    defined_specs_for,
    ordered_specs_for,
)
from src.capabilities.registry import CAPABILITY_REGISTRY, build_adapter

logger = logging.getLogger(__name__)

_PROBE_CACHE_TTL = 30.0  # seconds between availability re-evaluations


class CapabilityNotAvailable(RuntimeError):
    """Raised when no adapter for a capability category is currently available.

    Fail loud — never silently no-op when a capability is required.
    """


class CapabilityResolver:
    """Unified resolver across all capability categories."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # cache: category -> [(spec, adapter, ok, ts)]
        self._cache: Dict[str, List[tuple]] = {}

    def reload(self) -> None:
        """Invalidate the availability cache so the next resolve re-probes."""
        with self._lock:
            self._cache.clear()
        from src.providers.config import reload_config
        try:
            reload_config()
        except Exception as exc:
            logger.warning("Config reload failed: %s", exc)

    # ------------------------------------------------------------------
    # Core resolution
    # ------------------------------------------------------------------

    def _build_and_cache(self, category: str) -> List[tuple]:
        """Build adapter instances for all enabled specs in ``category`` and
        cache their current availability status."""
        specs = ordered_specs_for(category)
        entries = []
        for spec in specs:
            try:
                adapter = build_adapter(spec)
                ok = adapter.is_available()
                entries.append((spec, adapter, ok, time.monotonic()))
            except Exception as exc:
                logger.debug(
                    "capability resolver: skipping %s/%s — %s",
                    category, spec.key, exc,
                )
        return entries

    def _get_entries(self, category: str) -> List[tuple]:
        now = time.monotonic()
        with self._lock:
            entries = self._cache.get(category)
            if entries is not None:
                # Refresh stale entries in-place (keep order, update status).
                refreshed = []
                for spec, adapter, ok, ts in entries:
                    if (now - ts) > _PROBE_CACHE_TTL:
                        try:
                            ok = adapter.is_available()
                        except Exception:
                            ok = False
                        ts = now
                    refreshed.append((spec, adapter, ok, ts))
                self._cache[category] = refreshed
                return refreshed
            # First call — build and cache.
            entries = self._build_and_cache(category)
            self._cache[category] = entries
            return entries

    def resolve(
        self,
        category: str,
        *,
        exec_locus_pin: Optional[str] = None,
        cloud_shift: Optional[bool] = None,
    ) -> CapabilityAdapter:
        """Return the best available, jurisdiction-permitted adapter.

        Tries adapters in cost order (cheapest first). Raises
        ``CapabilityNotAvailable`` with a specific message when none qualify.
        """
        entries = self._get_entries(category)
        candidates = []
        for spec, adapter, ok, _ts in entries:
            if not ok:
                continue
            try:
                permitted = adapter.jurisdiction_allowed(
                    exec_locus_pin=exec_locus_pin, cloud_shift=cloud_shift
                )
            except Exception:
                permitted = True
            if permitted:
                candidates.append(adapter)

        if not candidates:
            available = [
                spec.key for spec, _, ok, _ in entries if ok
            ]
            blocked = [
                spec.key for spec, _, ok, _ in entries if not ok
            ]
            raise CapabilityNotAvailable(
                "No adapter available for capability category '%s'. "
                "Available (blocked by jurisdiction): %s. "
                "Unavailable (deps/secrets missing): %s." % (
                    category,
                    available or "none",
                    blocked or "none",
                )
            )

        return candidates[0]

    def resolve_all(self, category: str) -> List[CapabilityAdapter]:
        """Return all available, jurisdiction-permitted adapters for ``category``,
        cheapest first. Empty list when none qualify."""
        entries = self._get_entries(category)
        result = []
        for _spec, adapter, ok, _ts in entries:
            if ok:
                try:
                    if adapter.jurisdiction_allowed():
                        result.append(adapter)
                except Exception:
                    result.append(adapter)
        return result

    # ------------------------------------------------------------------
    # Capability matrix (for the dashboard UI)
    # ------------------------------------------------------------------

    def capability_matrix(self) -> Dict[str, Any]:
        """Return a full status dict for all capability categories.

        Shape::

            {
              "database": {
                "providers": [
                  {
                    "key": "postgresql",
                    "label": "PostgreSQL",
                    "exec_locus": "local",
                    "available": true,
                    "secrets_present": true,
                    "health": {"ok": true, "detail": "available"},
                    "required": false,
                  },
                  ...
                ],
                "active_key": "postgresql",
                "enabled_count": 1,
                "available_count": 1,
              },
              ...
            }
        """
        result: Dict[str, Any] = {}
        for category in ALL_CATEGORIES:
            required_keys = set(category_required_keys(category))
            entries = self._get_entries(category)
            providers = []
            active_key = None
            for spec, adapter, ok, _ts in entries:
                health = adapter.health_probe()
                providers.append({
                    "key": spec.key,
                    "label": spec.label or spec.key,
                    "adapter": spec.adapter,
                    "exec_locus": spec.exec_locus,
                    "available": ok,
                    "secrets_present": adapter.secrets_present(),
                    "health": health,
                    "required": spec.key in required_keys,
                    "cost": spec.cost,
                    "notes": spec.notes,
                })
                if ok and active_key is None:
                    # Apply the same jurisdiction gate as resolve() so the
                    # reported active_key matches what resolve() would actually
                    # return. A backend that is available but blocked by the
                    # current exec_locus_pin or cloud_shift policy must NOT
                    # be reported as the active provider.
                    try:
                        if adapter.jurisdiction_allowed():
                            active_key = spec.key
                    except Exception:
                        active_key = spec.key

            result[category] = {
                "providers": providers,
                "active_key": active_key,
                "enabled_count": len(providers),
                "available_count": sum(1 for p in providers if p["available"]),
            }
        return result

    # ------------------------------------------------------------------
    # Swap self-test diagnostic
    # ------------------------------------------------------------------

    def swap_self_test(self, category: str, preferred_key: str) -> Dict[str, Any]:
        """Prove a config-only provider swap takes effect with zero code change.

        Returns a dict with ``ok``, ``resolved_key``, ``detail``.
        """
        entries = self._get_entries(category)
        found = None
        for spec, adapter, ok, _ts in entries:
            if spec.key == preferred_key:
                found = (spec, adapter, ok)
                break
        if found is None:
            return {
                "ok": False,
                "resolved_key": None,
                "detail": "Provider '%s' is not enabled for category '%s'. "
                          "Add it to the [%s].enabled list in config/providers.toml."
                          % (preferred_key, category, category),
            }
        spec, adapter, ok = found
        return {
            "ok": ok,
            "resolved_key": preferred_key if ok else None,
            "detail": (
                "Provider '%s' is enabled and available for category '%s'. "
                "A config-only swap to this provider would take effect immediately."
                % (preferred_key, category)
            ) if ok else (
                "Provider '%s' is enabled but unavailable (secrets/deps missing): %s"
                % (preferred_key, adapter.health_probe().get("detail", "unknown"))
            ),
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_resolver: Optional[CapabilityResolver] = None
_resolver_lock = threading.Lock()


def get_resolver() -> CapabilityResolver:
    """Return the shared CapabilityResolver singleton."""
    global _resolver
    if _resolver is None:
        with _resolver_lock:
            if _resolver is None:
                _resolver = CapabilityResolver()
    return _resolver


def reset_resolver() -> None:
    """Drop the singleton (used by tests and after config reloads)."""
    global _resolver
    with _resolver_lock:
        _resolver = None
