"""Cognitive orchestrator capability adapter.

Wraps the existing ``src.swarm.orchestrator.WaveOrchestrator`` (the 5-wave,
9-role, 8-round debate cycle with full governance wiring) behind the unified
CapabilityAdapter interface. No behavior change — all governance scoring,
hallucination control, provenance tracking, and jurisdiction routing remain
in the original orchestrator. This adapter only surfaces its availability
through the unified capability layer.

Availability is gated on at least one LLM provider being reachable, since the
orchestrator requires LLM inference to function.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from src.capabilities.base import CapabilityAdapter, CapabilitySpec

logger = logging.getLogger(__name__)


class WaveOrchestratorAdapter(CapabilityAdapter):
    """Wraps WaveOrchestrator as a governed capability adapter.

    Available when at least one LLM provider adapter is reachable — the
    orchestrator is only as available as its underlying inference stack.
    """

    def _probe(self) -> bool:
        try:
            from src.providers.config import llm_provider_specs
            from src.providers.registry import build_adapter
            for spec in llm_provider_specs():
                try:
                    adapter = build_adapter(spec)
                    if adapter.is_available():
                        return True
                except Exception:
                    continue
            return False
        except Exception as exc:
            logger.debug("orchestrator probe failed: %s", exc)
            return False

    def health_probe(self) -> Dict[str, Any]:
        ok = self._probe()
        if ok:
            try:
                from src.providers.config import llm_provider_specs
                from src.providers.registry import build_adapter
                active = [
                    spec.key
                    for spec in llm_provider_specs()
                    for adapter in [build_adapter(spec)]
                    if adapter.is_available()
                ]
                detail = "available — live LLM providers: %s" % ", ".join(active)
            except Exception:
                detail = "available"
        else:
            detail = ("unavailable — no LLM provider is reachable; "
                      "the orchestrator requires at least one inference backend")
        return {
            "ok": ok,
            "detail": detail,
            "exec_locus": self.exec_locus,
        }

    async def run(self, prompt: str, **kwargs) -> dict:
        """Run the wave orchestrator for the given prompt.

        Delegates entirely to the existing WaveOrchestrator.run() so all
        governance wiring (compliance scoring, hallucination risk model,
        provenance, reflexive auditing, jurisdiction routing) is preserved.
        """
        from src.swarm.orchestrator import WaveOrchestrator
        orch = WaveOrchestrator()
        return await orch.run(prompt, **kwargs)
