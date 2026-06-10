"""Synchronous bridge to the governed LLM swarm for the coach sub-stack.

Coach modules are synchronous (mirroring finance), so routers drive them via
``asyncio.to_thread`` and the scheduler calls them directly in a worker thread.
``call_team_sync`` runs the async ``MultiProviderLLM.call_team`` to completion in
the current (worker) thread.

Governance:
  - All coach LLM output is governed: ``call_team`` scores each reply for
    compliance + hallucination and the judge weights/rejects accordingly.
  - Fails loud (``CoachLLMUnavailable``) when no provider is configured — never
    returns a fabricated answer.
  - ``strict_private=True`` pins jurisdiction so ONLY local providers may see the
    content (Private-Mode for inference); fails loud if no local model exists.

MUST be called from a thread WITHOUT a running event loop (worker thread or the
scheduler), because it uses ``asyncio.run``.
"""
import asyncio
import logging
from typing import Any, Dict, Optional

from src.swarm.llm_multi import MultiProviderLLM

logger = logging.getLogger(__name__)


class CoachLLMUnavailable(RuntimeError):
    """Raised when no usable (or no local, in strict mode) LLM provider exists."""


def call_team_sync(
    system: str,
    user: str,
    roles_hint: Optional[Dict[str, str]] = None,
    strict_private: bool = False,
) -> Dict[str, Any]:
    """Run the governed swarm and return its result dict. Fail loud."""
    llm = MultiProviderLLM()
    if strict_private:
        # Private-Mode for inference: drop all non-local providers so sensitive
        # emotional content is never sent to a cloud model.
        llm.apply_jurisdiction(cloud_shift_active=False)

    result = asyncio.run(llm.call_team(system, user, roles_hint)) or {}
    scores = result.get("compliance_scores") or {}
    merged = result.get("merged") or ""

    if not scores or "No providers configured" in merged:
        if strict_private:
            raise CoachLLMUnavailable(
                "Strict-private mode is on but no LOCAL LLM provider is "
                "available. Configure a local model or disable COACH_STRICT_PRIVATE."
            )
        raise CoachLLMUnavailable(
            "No LLM provider is configured. Add an LLM API key in the vault."
        )
    return result
