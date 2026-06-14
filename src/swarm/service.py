"""Single governed entry-point for running the LLM swarm.

Every caller that wants the swarm to *think* — the REST endpoints (the SPA's
``/api/tasks/run`` and the legacy form post) and the MCP server — funnels through
:func:`run_governed_swarm` so the governance pipeline (compliance scoring,
hallucination control, provenance, audit, jurisdiction + benchmark routing) is
applied IDENTICALLY everywhere. There is no second, ungoverned path: a tool that
exposed raw provider output would violate governance pillar 4 (every inference
governed). Transport concerns (DB rows, rate limiting, concurrency guards, auth)
stay in the callers; this module owns input normalization, fail-loud provider
checks, the orchestrator build, and the display-ready score projection.
"""
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Bound the prompt so a single request cannot push an unbounded payload into the
# (expensive) multi-provider fan-out. Canonical home for the limit — the web
# layer imports it from here so REST and MCP enforce the same ceiling.
MAX_PROMPT_LEN = 8000


class SwarmInputError(ValueError):
    """Raised for caller-fixable bad input (empty / over-long prompt).

    Carries a safe, user-facing message — never raw provider/internal detail.
    """


class SwarmUnavailable(RuntimeError):
    """Raised (fail-loud) when no enabled LLM provider has its secrets present.

    Honors governance pillar 1: refuse plainly rather than fake an answer.
    """


def llm_providers_available() -> bool:
    """True if at least one enabled LLM provider can ACTUALLY be called.

    Provider-agnostic (pillar 2): a *keyed* provider (OpenAI, Anthropic, ...)
    counts when all its required secrets resolve (connector proxy first, then
    vault/env); a *secretless* local provider (a sovereign-stack model such as
    ollama/mlx) counts only when its local server is actually listening — probed
    via the SAME adapter availability seam the swarm fanout uses, so a key-free
    local-only deployment (e.g. driving the MCP tool) is runnable too.

    The cheap, network-free secret check runs first and short-circuits, so a
    normal cloud deployment never pays for a local TCP probe. On a check-layer
    fault it fails *safe* (returns True) so a probe bug never blocks an
    otherwise-runnable swarm; the real error then surfaces loudly downstream.
    """
    try:
        from src.providers.config import llm_provider_specs
        from src.providers.secrets import has_secret

        secretless = []
        for spec in llm_provider_specs():
            if spec.secrets:
                if all(has_secret(s) for s in spec.secrets):
                    return True  # keyed provider ready — no probe needed
            else:
                secretless.append(spec)

        # No keyed provider configured. Fall back to probing secretless local
        # providers via their adapter (is_available() TCP-probes the local
        # server, cached briefly), so we never claim availability for a local
        # model whose server is down.
        if secretless:
            from src.providers.registry import build_adapter

            for spec in secretless:
                try:
                    if build_adapter(spec).is_available():
                        return True
                except Exception:
                    continue
        return False
    except Exception as e:
        logger.warning("Provider availability check failed: %s", e)
        return True


def normalize_scores(result: Dict[str, Any]) -> Dict[str, Any]:
    """Project the orchestrator's verbose result into a compact, display-ready
    governance summary shared by every surface (SPA result panel, MCP response).

    * ``compliance`` — 0..1 average of the per-claim 0..100 compliance scores.
    * ``hallucination_risk`` — 0..1 average risk.
    * ``jurisdiction`` — short ``domain · team`` string.
    * ``benchmark`` — compact benchmark-routing record (category, lead, specialty,
      classification signal, whether the capped bias actually fired).

    Keys are omitted when the corresponding governance field is absent, so the
    shape degrades gracefully rather than emitting nulls.
    """
    scores: Dict[str, Any] = {}

    cs_list = result.get("COMPLIANCE_SCORES")
    if isinstance(cs_list, list) and cs_list:
        cs_vals = [
            c.get("score") for c in cs_list
            if isinstance(c, dict) and isinstance(c.get("score"), (int, float))
        ]
        if cs_vals:
            scores["compliance"] = round(sum(cs_vals) / len(cs_vals) / 100.0, 3)

    hr = result.get("HALLUCINATION_RISK")
    if isinstance(hr, dict) and isinstance(hr.get("average"), (int, float)):
        scores["hallucination_risk"] = round(float(hr["average"]), 3)
    elif isinstance(hr, (int, float)):
        scores["hallucination_risk"] = round(float(hr), 3)

    jur = result.get("JURISDICTION")
    if isinstance(jur, dict):
        jur_parts = [str(jur.get(k)) for k in ("domain", "team") if jur.get(k)]
        if jur_parts:
            scores["jurisdiction"] = " \u00b7 ".join(jur_parts)
    elif isinstance(jur, str) and jur.strip():
        scores["jurisdiction"] = jur.strip()

    br = result.get("BENCHMARK_ROUTING")
    if isinstance(br, dict) and br.get("category"):
        scores["benchmark"] = {
            "category": br.get("category"),
            "lead_provider": br.get("lead_provider"),
            "lead_specialty": br.get("lead_specialty"),
            "classification_signal": br.get("classification_signal"),
            "bias_applied": bool(br.get("bias_applied")),
        }

    return scores


async def run_governed_swarm(
    prompt: str,
    domain: str = "general",
    team: str = "default",
    task_run_id: Optional[int] = None,
    *,
    check_providers: bool = True,
) -> Dict[str, Any]:
    """Run the full governed swarm for one prompt and return its structured result.

    Normalizes inputs, bound-checks the prompt, optionally verifies a provider is
    actually available (fail-loud), then builds the policy → broker → orchestrator
    stack and runs it. Returns the orchestrator's complete governance envelope
    (``SYNTHESIS`` plus compliance / hallucination / provenance / audit / sentinel
    / jurisdiction / benchmark fields). Callers project a compact view via
    :func:`normalize_scores`.

    Args:
        prompt: The task/goal. Stripped; must be non-empty and ``<= MAX_PROMPT_LEN``.
        domain: Operator domain hint; normalized to the jurisdiction vocabulary.
        team: Operator team hint; normalized to the jurisdiction vocabulary.
        task_run_id: Optional TaskRun row id for progress wiring (web only).
        check_providers: When True, raise :class:`SwarmUnavailable` if no enabled
            provider has its secrets. Web callers pre-check before creating a row,
            so this is the primary guard for non-web transports (e.g. MCP).

    Raises:
        SwarmInputError: empty or over-long prompt.
        SwarmUnavailable: ``check_providers`` and no provider is configured.
    """
    from src.swarm.jurisdiction import normalize_domain, normalize_team

    prompt = (prompt or "").strip()
    domain = normalize_domain(domain)
    team = normalize_team(team)

    if not prompt:
        raise SwarmInputError("Please enter a prompt.")
    if len(prompt) > MAX_PROMPT_LEN:
        raise SwarmInputError(
            "Prompt is too long (max %d characters)." % MAX_PROMPT_LEN
        )
    if check_providers and not llm_providers_available():
        raise SwarmUnavailable(
            "No LLM provider is configured. Add a provider key (e.g. OpenAI, "
            "Anthropic) before running the swarm."
        )

    from src.swarm.broker import CapabilityBroker
    from src.swarm.policy import PolicyEngine
    from src.swarm.integrations import Integrations
    from src.swarm.orchestrator import WaveOrchestrator

    policy = PolicyEngine()
    integrations = Integrations()
    broker = CapabilityBroker(policy, integrations)
    orchestrator = WaveOrchestrator(broker)
    return await orchestrator.run(
        prompt, domain=domain, team=team, task_run_id=task_run_id
    )
