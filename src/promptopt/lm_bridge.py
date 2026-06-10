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
import threading
from types import SimpleNamespace
from typing import Any, List, Optional

import dspy


class NoProviderAvailable(RuntimeError):
    """Raised when no LLM provider adapter is available — fail loud, never fake."""


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
