"""MCP server exposing MyDude's governed LLM swarm as a single reusable tool.

Run locally over stdio::

    python -m src.mcp.server

It registers exactly ONE tool — ``run_governed_swarm`` — which routes through the
same :func:`src.swarm.service.run_governed_swarm` path the web app uses. There is
deliberately NO raw-provider tool: every inference returned here has already gone
through compliance scoring, hallucination control, provenance, audit, jurisdiction
routing, and benchmark-aware lead selection (governance pillar 4). The full
governance envelope is returned alongside the synthesized answer so callers can
see and trust how the output was produced.

Transport is stdio (a local process), so no network auth is required. If this is
ever exposed over HTTP, add an auth token/verifier before doing so.
"""
import asyncio
import logging
from typing import Annotated, Any, Dict

from pydantic import Field

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from src.swarm.service import (
    MAX_PROMPT_LEN,
    SwarmInputError,
    SwarmUnavailable,
    normalize_scores,
    run_governed_swarm,
)

logger = logging.getLogger(__name__)

# Bound concurrent governed runs so an MCP client cannot saturate provider quotas
# or the event loop. Mirrors the web layer's concurrency guard; extra calls queue.
_MAX_CONCURRENT_RUNS = 2
_run_sem = asyncio.Semaphore(_MAX_CONCURRENT_RUNS)


def _valid_domains() -> str:
    """Comma-joined jurisdiction domains, for the tool's parameter description."""
    try:
        from src.swarm.jurisdiction import JURISDICTION_DOMAINS

        return ", ".join(sorted(JURISDICTION_DOMAINS))
    except Exception:  # pragma: no cover - description-only fallback
        return "general"


mcp = FastMCP(
    "mydude-governed-swarm",
    instructions=(
        "MyDude's governed multi-provider LLM swarm. Use the single "
        "`run_governed_swarm` tool to get a synthesized answer that has passed "
        "MyDude's governance pipeline (compliance scoring, hallucination control, "
        "provenance, audit, jurisdiction + benchmark-aware lead routing). The tool "
        "returns the synthesized answer plus the full governance envelope and a "
        "compact score summary; there is no raw, ungoverned provider access."
    ),
)


@mcp.tool(
    name="run_governed_swarm",
    title="Run the governed LLM swarm",
    description=(
        "Run a prompt through MyDude's governed multi-provider LLM swarm and return "
        "the synthesized answer together with its full governance envelope "
        "(compliance scores, hallucination risk, dissent, claim ledger, provenance, "
        "auditor + sentinel status, jurisdiction, and benchmark-aware lead routing). "
        "Every inference is governed — there is no raw provider passthrough. "
        "Returns a structured object: `synthesis` (the answer), `scores` (compact "
        "compliance/hallucination/jurisdiction/benchmark summary), and `governance` "
        "(the complete envelope)."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
    structured_output=True,
)
async def run_governed_swarm_tool(
    prompt: Annotated[
        str,
        Field(
            description="The task or question for the governed swarm.",
            min_length=1,
            max_length=MAX_PROMPT_LEN,
        ),
    ],
    domain: Annotated[
        str,
        Field(
            description=(
                "Operator domain hint that steers jurisdiction + benchmark routing. "
                "Known values: " + _valid_domains() + ". Unknown values fall back to "
                "'general'."
            ),
        ),
    ] = "general",
    team: Annotated[
        str,
        Field(description="Operator team hint (normalized; defaults to 'default')."),
    ] = "default",
) -> Dict[str, Any]:
    """Governed swarm entry for MCP clients. See the tool description."""
    async with _run_sem:
        try:
            result = await run_governed_swarm(
                prompt, domain=domain, team=team, check_providers=True
            )
        except SwarmInputError as e:
            # Caller-fixable: surface the safe, actionable message verbatim.
            raise ValueError(str(e))
        except SwarmUnavailable as e:
            raise ValueError(str(e))
        except Exception:
            # Never leak raw provider/internal detail to the client.
            logger.exception("Governed swarm run failed")
            raise RuntimeError(
                "The governed swarm failed to complete. Check the server logs for "
                "details."
            )

    return {
        "synthesis": result.get("SYNTHESIS", ""),
        "scores": normalize_scores(result),
        "governance": result,
    }


def main() -> None:
    """Entry point for ``python -m src.mcp.server`` — serve over stdio."""
    logging.basicConfig(level=logging.INFO)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
