"""Signature / prompt optimizer capability adapter.

Wraps the existing ``src.promptopt.lm_bridge`` (DSPy-powered signature
optimizer with CVE-2025-69872 cache hardening) behind the unified
CapabilityAdapter interface. No behavior change — DSPy, MIPROv2, GEPA, and
the hermetic optimizer testing infrastructure all remain in the original module.

Availability is gated on DSPy being importable AND at least one LLM provider
being available (the optimizer calls the LLM during optimization runs).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from src.capabilities.base import CapabilityAdapter, CapabilitySpec

logger = logging.getLogger(__name__)


class DSPyOptimizerAdapter(CapabilityAdapter):
    """Wraps the DSPy lm_bridge as a governed capability adapter.

    Available when dspy is importable and at least one LLM provider is live.
    The provider-agnostic bridge picks the first available LLM adapter — never
    a hardwired vendor (Governance Pillar #2).
    """

    def _probe(self) -> bool:
        try:
            import dspy  # noqa: F401 — availability check only
        except ImportError:
            logger.debug("sig_optimizer probe: dspy not importable")
            return False
        try:
            from src.promptopt.lm_bridge import available_provider
            provider = available_provider()
            return provider is not None
        except Exception as exc:
            logger.debug("sig_optimizer probe failed: %s", exc)
            return False

    def health_probe(self) -> Dict[str, Any]:
        ok = self._probe()
        if ok:
            try:
                from src.promptopt.lm_bridge import available_provider
                provider = available_provider()
                detail = "available — DSPy bridge ready (provider: %s)" % (provider or "unknown")
            except Exception:
                detail = "available (DSPy bridge)"
        else:
            try:
                import dspy  # noqa: F401
                detail = ("unavailable — dspy importable but no LLM provider "
                          "is reachable for optimization runs")
            except ImportError:
                detail = "unavailable — dspy package not installed"
        return {
            "ok": ok,
            "detail": detail,
            "exec_locus": self.exec_locus,
        }
