"""Synchronous bridge to the governed Cogitation entrypoint for the coach sub-stack.

Coach modules are synchronous (mirroring finance), so routers drive them via
``asyncio.to_thread`` and the scheduler calls them directly in a worker thread.
``call_team_sync`` runs the full Cogitation loop (WaveOrchestrator + provenance +
sentinel/auditor pass) to completion in the current (worker) thread.

Governance:
  - All coach LLM output flows through Cogitation.think() — the single governed
    cognition entrypoint — receiving the full multi-wave debate, provenance tree,
    compliance/hallucination scoring, sentinel/auditor pass, and DecisionTrace.
  - Fails loud (``CoachLLMUnavailable``) when no provider is configured — never
    returns a fabricated answer.
  - ``strict_private=True`` pins jurisdiction so ONLY local providers may see the
    content (Private-Mode for inference); fails loud if no local model exists.

MUST be called from a thread WITHOUT a running event loop (worker thread or the
scheduler), because it uses ``asyncio.run`` via Cogitation.think_sync().
"""
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class CoachLLMUnavailable(RuntimeError):
    """Raised when no usable (or no local, in strict mode) LLM provider exists."""


def call_team_sync(
    system: str,
    user: str,
    roles_hint: Optional[Dict[str, str]] = None,
    strict_private: bool = False,
) -> Dict[str, Any]:
    """Run the governed Cogitation loop and return a call_team-compatible dict.

    Routes through Cogitation.think_sync() → WaveOrchestrator, applying the
    full governance pass (waves, provenance, sentinel/auditor, DecisionTrace).
    The system prompt is passed as the persona_prompt so the coach's voice and
    citation rules are preserved inside the swarm's persona layer.

    Returns a dict with keys: merged, compliance_scores, hallucination_risks,
    replies, _cogitation_turn_id, _cogitation_trace_id.
    """
    from src.swarm.cogitation import Cogitation, CogitationContext

    ctx = CogitationContext(
        source="coach",
        strict_private=strict_private,
        domain="coach",
        team="default",
        persona_prompt=system,
    )

    cog_result = Cogitation().think_sync(user, ctx)

    merged = cog_result.merged
    scores = cog_result.compliance_scores

    if cog_result.outcome == "error" or not cog_result.result or not cog_result.result.get("FACTS") and not cog_result.result.get("DECISIONS"):
        if "No providers configured" in merged or not scores:
            if strict_private:
                raise CoachLLMUnavailable(
                    "Strict-private mode is on but no LOCAL LLM provider is "
                    "available. Configure a local model or disable COACH_STRICT_PRIVATE."
                )
            raise CoachLLMUnavailable(
                "No LLM provider is configured. Add an LLM API key in the vault."
            )

    return cog_result.as_call_team_dict()
