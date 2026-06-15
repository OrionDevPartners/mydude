"""LLM capability adapter — bridge to the existing multi-provider swarm stack.

Wraps the existing ``src.providers`` package (LLMAdapter, registry, config)
behind the unified CapabilityAdapter interface with ZERO behavior change.
The actual swarm logic (consensus, governance scoring, hallucination control,
jurisdiction routing, benchmark-aware lead routing) lives in the original
stack and is completely preserved.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.capabilities.base import CapabilityAdapter, CapabilitySpec


class LLMCapabilityAdapter(CapabilityAdapter):
    """Wraps the existing LLM provider adapter (src.providers) behind the
    unified CapabilityAdapter interface.

    Each instance corresponds to one provider/backend entry from env_1 (e.g.
    openai, anthropic, gemini, …). Availability is delegated to the concrete
    LLMAdapter.is_available() so the existing TCP probe / secret logic is
    preserved intact.
    """

    def __init__(self, spec: CapabilitySpec) -> None:
        super().__init__(spec)
        self._llm_adapter: Optional[object] = None
        self._built = False

    def _get_llm_adapter(self):
        """Lazily build the underlying LLMAdapter from the existing stack."""
        if self._built:
            return self._llm_adapter
        self._built = True
        try:
            from src.providers.config import defined_provider_specs
            from src.providers.registry import build_adapter as _build_llm_adapter

            specs = defined_provider_specs()
            pspec = specs.get(self.spec.key)
            if pspec is None:
                self._llm_adapter = None
                return None
            self._llm_adapter = _build_llm_adapter(pspec)
        except Exception:
            self._llm_adapter = None
        return self._llm_adapter

    def secrets_present(self) -> bool:
        adapter = self._get_llm_adapter()
        if adapter is None:
            return False
        try:
            return adapter.secrets_present()
        except Exception:
            return False

    def _probe(self) -> bool:
        adapter = self._get_llm_adapter()
        if adapter is None:
            return False
        try:
            return adapter.is_available()
        except Exception:
            return False

    def is_available(self) -> bool:
        """Delegate entirely to the existing LLMAdapter availability logic."""
        try:
            adapter = self._get_llm_adapter()
            if adapter is None:
                return False
            return adapter.is_available()
        except Exception:
            return False

    def health_probe(self) -> Dict[str, Any]:
        ok = self.is_available()
        adapter = self._get_llm_adapter()
        model = ""
        if adapter is not None:
            try:
                model = getattr(adapter, "_model", "") or ""
            except Exception:
                pass
        return {
            "ok": ok,
            "detail": ("available" + (" model=%s" % model if model else ""))
                      if ok else "unavailable (secret missing or client build failed)",
            "exec_locus": self.spec.exec_locus,
        }

    @property
    def exec_locus(self) -> str:
        return self.spec.exec_locus
