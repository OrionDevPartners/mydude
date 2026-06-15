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

import asyncio
import inspect
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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


class CapabilityDenied(RuntimeError):
    """Raised when a governed capability call is rejected by policy.

    Distinct from :class:`CapabilityNotAvailable` (which means *no provider*):
    this means a provider exists but the specific invocation is disallowed —
    e.g. a container_compute command outside the allow-list. Fail loud so the
    caller refuses to act rather than silently bypassing governance.
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
        # Phase 1 — gather cached entries per category (cheap; availability is
        # served from the short-lived probe cache).
        cat_entries: Dict[str, tuple] = {}
        for category in ALL_CATEGORIES:
            required_keys = set(category_required_keys(category))
            cat_entries[category] = (required_keys, self._get_entries(category))

        # Phase 2 — run the live per-provider probes (health_probe / secrets /
        # jurisdiction) concurrently. These are independent network/IO calls;
        # running them serially across every provider in all categories made
        # the matrix endpoint take ~18s. A thread pool collapses that to roughly
        # the slowest single probe while keeping the data live.
        tasks: List[tuple] = []
        for category, (required_keys, entries) in cat_entries.items():
            for idx, (spec, adapter, ok, _ts) in enumerate(entries):
                tasks.append((category, idx, spec, adapter, ok, required_keys))

        def _probe(task: tuple) -> tuple:
            category, idx, spec, adapter, ok, required_keys = task
            try:
                health = adapter.health_probe()
            except Exception as exc:
                health = {"ok": False, "detail": str(exc)}
            try:
                secrets_present = adapter.secrets_present()
            except Exception:
                secrets_present = False
            # Apply the same jurisdiction gate as resolve() so the reported
            # active_key matches what resolve() would actually return. A backend
            # that is available but blocked by the current exec_locus_pin or
            # cloud_shift policy must NOT be reported as the active provider.
            try:
                jurisdiction_ok = adapter.jurisdiction_allowed()
            except Exception:
                jurisdiction_ok = True
            provider = {
                "key": spec.key,
                "label": spec.label or spec.key,
                "adapter": spec.adapter,
                "exec_locus": spec.exec_locus,
                "available": ok,
                "secrets_present": secrets_present,
                "health": health,
                "required": spec.key in required_keys,
                "cost": spec.cost,
                "notes": spec.notes,
            }
            return category, idx, provider, ok, jurisdiction_ok

        probed: List[tuple] = []
        if tasks:
            with ThreadPoolExecutor(max_workers=min(16, len(tasks))) as pool:
                probed = list(pool.map(_probe, tasks))

        # Phase 3 — assemble, preserving each category's cost-ordered sequence.
        by_cat: Dict[str, List[tuple]] = {c: [] for c in ALL_CATEGORIES}
        for category, idx, provider, ok, jurisdiction_ok in probed:
            by_cat[category].append((idx, provider, ok, jurisdiction_ok))

        result: Dict[str, Any] = {}
        for category in ALL_CATEGORIES:
            rows = sorted(by_cat[category], key=lambda r: r[0])
            providers = [r[1] for r in rows]
            active_key = None
            for _idx, provider, ok, jurisdiction_ok in rows:
                if ok and active_key is None and jurisdiction_ok:
                    active_key = provider["key"]
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


# ---------------------------------------------------------------------------
# Governed capability invocation
# ---------------------------------------------------------------------------
#
# Governance pillar #4 requires EVERY capability invocation — not just LLM and
# browser calls — to be governed: jurisdiction-gated, allow-list-enforced, and
# recorded to the audit trail with caller identity. The non-LLM categories
# (container_compute, realtime, database, object_storage, …) are resolved
# directly via :func:`get_resolver` and would otherwise bypass that stack.
# ``governed_resolve`` and ``governed_call`` close that gap.


def governed_resolve(
    category: str,
    *,
    exec_locus_pin: Optional[str] = None,
    cloud_shift: Optional[bool] = None,
    actor: Optional[Dict[str, Any]] = None,
    source: Optional[str] = None,
) -> CapabilityAdapter:
    """Resolve an adapter for ``category`` with jurisdiction governance.

    Thin governed wrapper over :meth:`CapabilityResolver.resolve`. The resolver
    already filters out adapters blocked by the cloud-shift kill switch or
    ``exec_locus_pin`` and raises :class:`CapabilityNotAvailable` when none
    qualify — so an out-of-jurisdiction request is rejected here. The rejection
    is recorded to the audit trail before the exception propagates.
    """
    actor = actor or {}
    try:
        return get_resolver().resolve(
            category, exec_locus_pin=exec_locus_pin, cloud_shift=cloud_shift,
        )
    except CapabilityNotAvailable as exc:
        _audit(
            capability="capability:%s" % category,
            status="blocked",
            detail=str(exc),
            source=source,
            actor=actor,
        )
        raise


def governed_call(
    category: str,
    method: str,
    *args,
    exec_locus_pin: Optional[str] = None,
    cloud_shift: Optional[bool] = None,
    actor: Optional[Dict[str, Any]] = None,
    source: Optional[str] = None,
    **kwargs,
):
    """Governed entry point for any non-LLM capability invocation.

    Steps (governance pillar #4):
      1. Resolve a jurisdiction-permitted adapter (out-of-jurisdiction → reject).
      2. Enforce the CapabilityBroker command allow-list for container_compute.
      3. Record the invocation (exec_locus, status, caller identity) to the audit
         trail — before and after — so blocked, failed, and successful calls are
         all captured.
      4. Invoke ``adapter.<method>(*args, **kwargs)`` and return its result.

    Raises :class:`CapabilityNotAvailable` (no permitted provider) or
    :class:`CapabilityDenied` (provider exists but the call is disallowed).
    Never silently no-ops — fail loud.
    """
    actor = actor or {}
    fn, exec_locus, backend, target = _governed_prepare(
        category, method, args, kwargs,
        exec_locus_pin=exec_locus_pin, cloud_shift=cloud_shift,
        actor=actor, source=source,
    )

    # ``governed_call`` is synchronous: it audits "ok" immediately after ``fn``
    # returns. An async (coroutine) method would return un-awaited, so the audit
    # would record success before the work actually ran (or failed) — a
    # governance lie. Reject loudly and point the caller at ``governed_call_async``.
    if inspect.iscoroutinefunction(fn):
        reason = (
            "Method '%s.%s' is asynchronous; call it via governed_call_async so "
            "its real outcome is audited." % (category, method)
        )
        _audit(
            capability="%s:%s" % (category, method), target=target, backend=backend,
            status="error", detail=reason, source=source,
            exec_locus=exec_locus, actor=actor,
        )
        raise CapabilityDenied(reason)

    try:
        result = fn(*args, **kwargs)
        # Defensive: a non-``async def`` callable can still return an awaitable
        # (e.g. a method returning a coroutine). Treat that the same way.
        if inspect.isawaitable(result):
            _close_awaitable(result)
            reason = (
                "Method '%s.%s' returned an awaitable; call it via "
                "governed_call_async." % (category, method)
            )
            _audit(
                capability="%s:%s" % (category, method), target=target, backend=backend,
                status="error", detail=reason, source=source,
                exec_locus=exec_locus, actor=actor,
            )
            raise CapabilityDenied(reason)
    except Exception as exc:
        if not isinstance(exc, CapabilityDenied):
            _audit(
                capability="%s:%s" % (category, method), target=target, backend=backend,
                status="error", detail=str(exc), source=source,
                exec_locus=exec_locus, actor=actor,
            )
        raise

    _audit(
        capability="%s:%s" % (category, method), target=target, backend=backend,
        status="ok", source=source, exec_locus=exec_locus, actor=actor,
    )
    return result


async def governed_call_async(
    category: str,
    method: str,
    *args,
    exec_locus_pin: Optional[str] = None,
    cloud_shift: Optional[bool] = None,
    actor: Optional[Dict[str, Any]] = None,
    source: Optional[str] = None,
    **kwargs,
):
    """Async-aware counterpart to :func:`governed_call`.

    Same governance contract (jurisdiction gate, allow-list, audit of every
    outcome), but the invocation is awaited so the audit reflects the *real*
    result:

      * a coroutine method (``async def``) is awaited;
      * a synchronous/blocking method is offloaded with ``asyncio.to_thread`` so
        it never blocks the event loop.

    Use this from async call sites (e.g. telephony/realtime). Returns the
    method's result; raises :class:`CapabilityNotAvailable` or
    :class:`CapabilityDenied` like the sync variant.
    """
    actor = actor or {}
    fn, exec_locus, backend, target = _governed_prepare(
        category, method, args, kwargs,
        exec_locus_pin=exec_locus_pin, cloud_shift=cloud_shift,
        actor=actor, source=source,
    )

    try:
        if inspect.iscoroutinefunction(fn):
            result = await fn(*args, **kwargs)
        else:
            result = await asyncio.to_thread(fn, *args, **kwargs)
            # A sync callable may still hand back an awaitable.
            if inspect.isawaitable(result):
                result = await result
    except Exception as exc:
        _audit(
            capability="%s:%s" % (category, method), target=target, backend=backend,
            status="error", detail=str(exc), source=source,
            exec_locus=exec_locus, actor=actor,
        )
        raise

    _audit(
        capability="%s:%s" % (category, method), target=target, backend=backend,
        status="ok", source=source, exec_locus=exec_locus, actor=actor,
    )
    return result


def _governed_prepare(category, method, args, kwargs, *,
                      exec_locus_pin=None, cloud_shift=None, actor=None, source=None):
    """Shared pre-invocation governance for governed_call(_async).

    Resolves a jurisdiction-permitted adapter, enforces the container_compute
    allow-list, and verifies the method exists. Audits and raises on any
    rejection. Returns ``(fn, exec_locus, backend, target)`` ready to invoke.
    """
    actor = actor or {}
    adapter = governed_resolve(
        category, exec_locus_pin=exec_locus_pin, cloud_shift=cloud_shift,
        actor=actor, source=source,
    )
    exec_locus = getattr(adapter, "exec_locus", None)
    backend = getattr(adapter, "key", None)
    target = _call_target(category, method, args, kwargs)

    # Allow-list enforcement for command execution (container_compute).
    if category == "container_compute" and method in ("run_command", "run_command_async"):
        command = args[0] if args else kwargs.get("command")
        decision = _enforce_compute_allowlist(command)
        if not decision.allowed:
            _audit(
                capability="container_compute:%s" % method,
                target=target, backend=backend, status="blocked",
                detail=decision.reason, source=source,
                exec_locus=exec_locus, actor=actor,
            )
            raise CapabilityDenied(decision.reason)

    fn = getattr(adapter, method, None)
    if fn is None or not callable(fn):
        reason = "Adapter for '%s' has no callable method '%s'." % (category, method)
        _audit(
            capability="%s:%s" % (category, method), target=target, backend=backend,
            status="error", detail=reason, source=source,
            exec_locus=exec_locus, actor=actor,
        )
        raise CapabilityDenied(reason)
    return fn, exec_locus, backend, target


def _close_awaitable(obj):
    """Best-effort close of an un-awaited coroutine to avoid a 'never awaited'
    warning when we reject an async method on the sync path."""
    try:
        close = getattr(obj, "close", None)
        if callable(close):
            close()
    except Exception:  # pragma: no cover - defensive cleanup only
        pass


def _enforce_compute_allowlist(command):
    """Run the CapabilityBroker's command allow-list over a compute command.

    Returns the PolicyDecision. Defaults to a safe denial if the policy layer
    cannot be loaded — never fail open.
    """
    try:
        from src.swarm.policy import PolicyEngine, PolicyDecision
    except Exception as exc:  # pragma: no cover - policy layer must be present
        from types import SimpleNamespace
        return SimpleNamespace(allowed=False, reason="Policy engine unavailable: %s" % exc)
    return PolicyEngine().evaluate_compute_command(command)


def _call_target(category: str, method: str, args, kwargs) -> Optional[str]:
    """Best-effort, credential-free description of the call's target for the
    audit trail. Commands are summarised; never log raw kwargs (may carry
    secrets)."""
    if category == "container_compute":
        command = args[0] if args else kwargs.get("command")
        if isinstance(command, (list, tuple)):
            return " ".join(str(t) for t in command)[:2000]
        if command is not None:
            return str(command)[:2000]
    if args:
        return str(args[0])[:2000]
    return None


def _audit(capability, *, target=None, backend=None, status="ok", detail=None,
           source=None, exec_locus=None, actor=None):
    """Write a governed-call record to the CapabilityAuditLog. Fail-soft — an
    audit-write failure must never break (or, by raising, silently abort) the
    governed call path."""
    actor = actor or {}
    try:
        from src.swarm.integrations import audit_capability
        audit_capability(
            capability, target=target, backend=backend, status=status,
            detail=detail, source=source, exec_locus=exec_locus,
            actor_user_id=actor.get("uid"),
            actor_username=actor.get("username"),
        )
    except Exception as exc:  # pragma: no cover - audit must never break the call
        logger.warning("governed_call audit failed: %s", exc)
