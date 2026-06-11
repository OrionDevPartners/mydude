"""Bridge DSPy to the project's provider-agnostic adapter registry.

Pillars: provider-agnostic (never hardwired to a vendor — picks the first
available adapter from the registry) and fail-loud (raises NoProviderAvailable
instead of silently degrading). DSPy never sees a raw secret: the adapter sources
its credential through the existing connector-proxy/vault chain.

Threading model: optimizers call the LM synchronously from worker threads, but
the adapters are async. We run every adapter coroutine on ONE persistent daemon
event loop and block on the result via run_coroutine_threadsafe. The runtime
judge path is already async, so aforward() awaits the adapter directly on the
caller's loop. A runtime LM and an optimizer LM are therefore separate instances
so their lazily-built async clients never straddle two event loops.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from types import SimpleNamespace
from typing import Any, List, Optional

import dspy

logger = logging.getLogger(__name__)


class NoProviderAvailable(RuntimeError):
    """Raised when no LLM provider adapter is available — fail loud, never fake."""


# --- diskcache CVE mitigation (CVE-2025-69872 / GHSA-w8v5-vhqr-4h9v) ----------
# DSPy's on-disk cache is backed by the `diskcache` package, which deserializes
# entries with unrestricted ``pickle`` and has NO upstream patch (PyPI's latest
# release is the vulnerable 5.6.3, and every dspy release hard-requires
# diskcache>=5.6.0, so it cannot be upgraded or removed from the closure).
# ``dspy.configure_cache(restrict_pickle=True)`` confines disk-cache
# deserialization to safe types, closing the arbitrary-code-execution path while
# leaving caching behaviour intact. Idempotent + fail-soft: a hardening failure
# must never crash inference, but is logged loudly for the audit trail.
_cache_hardened = False
_cache_lock = threading.Lock()


def harden_dspy_cache() -> bool:
    """Force DSPy's disk cache to use a restricted unpickler.

    Returns True once the on-disk unsafe-pickle surface is closed — either by
    installing the restricted unpickler, or, if that fails, by disabling the
    on-disk cache entirely. Safe to call repeatedly (only the first successful
    call reconfigures the cache). Fails CLOSED: it never returns with the
    unrestricted-pickle disk cache live, and raises if it can neither restrict
    nor disable it (fail loud rather than run a known RCE surface).
    """
    global _cache_hardened
    if _cache_hardened:
        return True
    with _cache_lock:
        if _cache_hardened:
            return True
        try:
            dspy.configure_cache(restrict_pickle=True)
            _cache_hardened = True
            logger.info(
                "DSPy disk cache hardened (restrict_pickle=True) — diskcache "
                "CVE-2025-69872 unsafe-pickle path neutralized"
            )
            return _cache_hardened
        except Exception as primary_exc:
            # Fail CLOSED: never leave the unrestricted-pickle disk cache live
            # (that IS the CVE-2025-69872 RCE surface). Drop the on-disk cache
            # entirely — the in-memory cache keeps working — and only raise if
            # even that fails, because silently serving inference over the
            # vulnerable disk cache would violate the fail-loud governance pillar.
            logger.error(
                "Could not apply restrict_pickle to DSPy disk cache (%s); "
                "disabling the on-disk cache to remove the CVE-2025-69872 "
                "unsafe-pickle surface", primary_exc
            )
            try:
                dspy.configure_cache(enable_disk_cache=False)
                _cache_hardened = True
                logger.warning(
                    "DSPy on-disk cache DISABLED (fail-closed fallback); "
                    "CVE-2025-69872 surface removed, in-memory cache still active"
                )
                return _cache_hardened
            except Exception as fallback_exc:
                logger.critical(
                    "Could neither restrict nor disable the DSPy disk cache; "
                    "refusing to run with the unrestricted-pickle CVE-2025-69872 "
                    "surface live: %s", fallback_exc
                )
                raise


# Harden as soon as the governed DSPy bridge is imported, so every dspy
# disk-cache user (server, optimizer threads, tests, CLI) goes through the
# restricted unpickler regardless of entry path.
harden_dspy_cache()


# --- ONE persistent daemon event loop for sync (optimizer) adapter calls -------
_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_lock = threading.Lock()


def _persistent_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _loop_lock:
        if _loop is None or not _loop.is_running():
            _loop = asyncio.new_event_loop()
            t = threading.Thread(
                target=_loop.run_forever, name="promptopt-lm-loop", daemon=True
            )
            t.start()
        return _loop


def available_provider() -> Optional[str]:
    """Return the key of the first available provider adapter, or None."""
    try:
        from src.providers.config import llm_provider_specs
        from src.providers.registry import build_adapter
        for spec in llm_provider_specs():
            try:
                if build_adapter(spec).is_available():
                    return getattr(spec, "key", None) or getattr(spec, "name", None)
            except Exception:
                continue
    except Exception:
        return None
    return None


def _build_first_available_adapter(provider_key: Optional[str] = None):
    """Build a FRESH adapter for the first available provider (or a named one)."""
    from src.providers.config import llm_provider_specs
    from src.providers.registry import build_adapter
    specs = llm_provider_specs()
    for spec in specs:
        key = getattr(spec, "key", None) or getattr(spec, "name", None)
        if provider_key and key != provider_key:
            continue
        try:
            adapter = build_adapter(spec)
        except Exception:
            continue
        try:
            if adapter.is_available():
                return adapter
        except Exception:
            continue
    raise NoProviderAvailable(
        "No LLM provider is available for the prompt engine. Add a working API "
        "key (or connect a provider) before running optimization or the governed "
        "judge."
    )


def _openai_response(text: str, model: str) -> Any:
    """Minimal OpenAI-/litellm-style response object DSPy's BaseLM consumes."""
    message = SimpleNamespace(role="assistant", content=text or "", tool_calls=None)
    choice = SimpleNamespace(message=message, finish_reason="stop", index=0, text=text or "")
    usage = SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0)
    return SimpleNamespace(
        choices=[choice], usage=usage, model=model, id="promptopt", object="chat.completion"
    )


class ProviderBackedLM(dspy.BaseLM):
    """A dspy.BaseLM that delegates to a project provider adapter.

    Set ``runtime=True`` for the async server path (aforward on the caller's
    loop); leave it False for optimizer threads (forward on the persistent loop).
    """

    def __init__(
        self,
        provider_key: Optional[str] = None,
        runtime: bool = False,
        max_tokens: int = 1500,
        model: str = "promptopt/governed",
        **kwargs,
    ):
        super().__init__(model=model, max_tokens=max_tokens, cache=False, **kwargs)
        self._provider_key = provider_key
        self._runtime = runtime
        self._budget = max_tokens
        self._adapter = None
        self._adapter_lock = threading.Lock()

    # -- message handling -----------------------------------------------------
    @staticmethod
    def _split(prompt, messages) -> (str):
        if messages:
            sys_parts: List[str] = []
            user_parts: List[str] = []
            for m in messages:
                role = (m.get("role") if isinstance(m, dict) else None) or "user"
                content = m.get("content") if isinstance(m, dict) else str(m)
                if isinstance(content, list):
                    content = " ".join(
                        c.get("text", "") if isinstance(c, dict) else str(c) for c in content
                    )
                if role == "system":
                    sys_parts.append(content or "")
                else:
                    user_parts.append(content or "")
            system = "\n\n".join(p for p in sys_parts if p) or "You are a helpful assistant."
            user = "\n\n".join(p for p in user_parts if p)
            return system, user
        return "You are a helpful assistant.", (prompt or "")

    def _ensure_adapter(self):
        if self._adapter is None:
            with self._adapter_lock:
                if self._adapter is None:
                    self._adapter = _build_first_available_adapter(self._provider_key)
        return self._adapter

    async def _agenerate(self, system: str, user: str) -> str:
        adapter = self._ensure_adapter()
        try:
            await adapter.resolve_model()
        except Exception:
            pass
        text = await adapter.generate(system, user, self._budget)
        key = getattr(adapter, "key", "provider")
        mdl = getattr(adapter, "model", "model")
        self.model = "%s/%s" % (key, mdl)
        return text or ""

    # -- DSPy entry points ----------------------------------------------------
    def forward(self, prompt=None, messages=None, **kwargs):
        system, user = self._split(prompt, messages)
        fut = asyncio.run_coroutine_threadsafe(
            self._agenerate(system, user), _persistent_loop()
        )
        text = fut.result()
        return _openai_response(text, self.model)

    async def aforward(self, prompt=None, messages=None, **kwargs):
        system, user = self._split(prompt, messages)
        text = await self._agenerate(system, user)
        return _openai_response(text, self.model)
