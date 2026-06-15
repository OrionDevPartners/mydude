"""
Governed knowledge-siphon for the headless MCP server.

Every successful MCP capability interaction is distilled into a COMPACT,
non-secret memory claim and written into MyDude's long-term semantic memory, so
the brain self-improves from its own headless use. This is purely additive: it
never alters the capability's result and never raises into the request path.

Governance (pillars #1 & #4):
  * Governed completions (``azure_aoai_complete``) are siphoned ONLY when the
    swarm's own compliance/hallucination scores clear the bar — ungoverned or
    sub-threshold output is skipped, never stored raw.
  * Read / deploy tools store a compact PROVENANCE summary only — never raw rows,
    SQL, query literals, params, prompts (beyond a short excerpt), bearer/plan
    tokens, or full model envelopes.
  * ``memory_*`` capabilities are never siphoned (no recall->write feedback loop).
  * Failures fail soft + audited (``mcp_memory_siphon``) — the request is sacred.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Governed-completion thresholds (pillar #4): only knowledge the swarm itself
# scored as compliant + low-hallucination is admitted into long-term memory.
MIN_COMPLIANCE = 0.80
MAX_HALLUCINATION_RISK = 0.25

# A siphoned claim that contradicts existing memory is KEPT (provenance matters)
# but down-weighted and flagged for the contradiction surface rather than dropped.
_CONTRADICTION_CONF_CAP = 0.3

#: Single category for siphoned interactions so recall can target/exclude them.
SIPHON_CATEGORY = "mcp_interaction"


def _bounded(text: Any, limit: int) -> str:
    return str(text or "").strip()[:limit]


def build_siphon_claim(
    capability: str,
    params: Dict[str, Any],
    data: Any,
) -> Optional[Dict[str, Any]]:
    """Distill one capability interaction into a sanitized memory-claim candidate.

    Returns a dict ``{content, category, confidence, source, metadata}`` ready for
    ``write_claim``, or ``None`` when nothing should be siphoned (excluded
    capability, failed call, or ungoverned / sub-threshold completion).
    """
    if not capability or capability.startswith("memory_"):
        return None  # never siphon memory reads — no recall->write loop
    if not isinstance(data, dict):
        return None
    # Defensive: only siphon successful interactions. The MCP dispatcher already
    # raises on ok=False, but guard anyway so this is safe to call from anywhere.
    if data.get("ok") is False:
        return None

    params = params or {}
    source = "mcp:%s" % capability

    if capability == "azure_aoai_complete":
        return _claim_for_completion(params, data, source)

    if capability == "azure_cosmos_read":
        db = _bounded(params.get("database"), 80)
        container = _bounded(params.get("container"), 80)
        count = data.get("count")
        if count is None and isinstance(data.get("items"), list):
            count = len(data["items"])
        return {
            "content": "Cosmos read on %s/%s returned %s item(s)." % (
                db or "?", container or "?", count if count is not None else "?"),
            "category": SIPHON_CATEGORY,
            "confidence": 0.6,
            "source": source,
            "metadata": {
                "capability": capability, "kind": "read_summary",
                "database": db, "container": container,
                "count": count, "truncated": bool(data.get("truncated")),
            },
        }

    if capability == "azure_pg_select":
        db_key = _bounded(params.get("db_key"), 80)
        rowcount = data.get("rowcount")
        if rowcount is None and isinstance(data.get("rows"), list):
            rowcount = len(data["rows"])
        ncols = len(data["columns"]) if isinstance(data.get("columns"), list) else None
        return {
            "content": "Postgres SELECT on '%s' returned %s row(s)." % (
                db_key or "?", rowcount if rowcount is not None else "?"),
            "category": SIPHON_CATEGORY,
            "confidence": 0.6,
            "source": source,
            "metadata": {
                "capability": capability, "kind": "read_summary",
                "db_key": db_key, "rowcount": rowcount, "column_count": ncols,
                "truncated": bool(data.get("truncated")),
            },
        }

    if capability == "azure_deploy_status":
        state = _bounded(data.get("state"), 60)
        deployment = _bounded(data.get("deployment") or "mydude", 80)
        return {
            "content": "Azure deployment '%s' state: %s." % (deployment, state or "unknown"),
            "category": SIPHON_CATEGORY,
            "confidence": 0.7,
            "source": source,
            "metadata": {
                "capability": capability, "kind": "deploy_status",
                "deployment": deployment, "state": state,
            },
        }

    if capability == "azure_deploy_plan":
        # NEVER siphon plan_token / plan_hash / confirm phrase (secrets).
        change_count = data.get("change_count")
        return {
            "content": "Azure deploy plan computed: %s change(s)." % (
                change_count if change_count is not None else "?"),
            "category": SIPHON_CATEGORY,
            "confidence": 0.7,
            "source": source,
            "metadata": {
                "capability": capability, "kind": "deploy_plan",
                "change_count": change_count,
            },
        }

    if capability == "azure_deploy_apply":
        status = _bounded(data.get("status") or data.get("state"), 60)
        return {
            "content": "Azure deploy applied: %s." % (status or "unknown"),
            "category": SIPHON_CATEGORY,
            "confidence": 0.75,
            "source": source,
            "metadata": {
                "capability": capability, "kind": "deploy_apply", "status": status,
            },
        }

    # Forward-compatible default (pillar #6): record a minimal provenance trace
    # for any FUTURE tool WITHOUT echoing its (unknown-shape) data values.
    return {
        "content": "MCP interaction '%s' completed successfully." % capability,
        "category": SIPHON_CATEGORY,
        "confidence": 0.5,
        "source": source,
        "metadata": {"capability": capability, "kind": "generic"},
    }


def _claim_for_completion(
    params: Dict[str, Any], data: Dict[str, Any], source: str,
) -> Optional[Dict[str, Any]]:
    """Governed-completion siphon: admit ONLY swarm output that clears the
    compliance / hallucination bar; store the governed SYNTHESIS, never the full
    envelope or raw model text (pillar #4)."""
    envelope = data.get("result")
    if not isinstance(envelope, dict):
        return None  # no governed envelope -> nothing trustworthy to store
    synthesis = envelope.get("SYNTHESIS")
    if not isinstance(synthesis, str) or not synthesis.strip():
        return None
    try:
        from src.swarm.service import normalize_scores
        scores = normalize_scores(envelope)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("siphon: normalize_scores failed: %s", e)
        return None
    compliance = scores.get("compliance")
    hr = scores.get("hallucination_risk")
    # Missing scores => ungoverned => never siphon (fail-closed, pillar #4).
    if not isinstance(compliance, (int, float)) or not isinstance(hr, (int, float)):
        return None
    if compliance < MIN_COMPLIANCE or hr > MAX_HALLUCINATION_RISK:
        return None
    meta: Dict[str, Any] = {
        "capability": "azure_aoai_complete",
        "kind": "governed_completion",
        "compliance": round(float(compliance), 3),
        "hallucination_risk": round(float(hr), 3),
        "prompt_excerpt": _bounded(params.get("prompt"), 200),
    }
    if scores.get("jurisdiction"):
        meta["jurisdiction"] = _bounded(scores.get("jurisdiction"), 80)
    return {
        "content": _bounded(synthesis, 1000),
        "category": SIPHON_CATEGORY,
        "confidence": round(float(compliance), 3),
        "source": source,
        "metadata": meta,
    }


def siphon_interaction(
    capability: str,
    params: Dict[str, Any],
    data: Any,
    *,
    substrate: Any = None,
) -> Optional[str]:
    """Build + persist a sanitized claim for one MCP interaction.

    Synchronous (the memory substrate is sync); call it from async code via
    ``asyncio.to_thread``. Returns the new memory id, or ``None`` if nothing was
    siphoned. Never raises — the caller's request path must be unaffected.
    """
    try:
        candidate = build_siphon_claim(capability, params or {}, data)
        if candidate is None:
            return None
        if substrate is None:
            from src.memory.substrate import get_substrate
            substrate = get_substrate()

        content = candidate["content"]
        confidence = float(candidate["confidence"])
        meta = dict(candidate.get("metadata") or {})

        # Contradiction gate: run the SAME semantic check the swarm uses. A
        # contradicted siphon is kept (provenance) but down-weighted + flagged.
        try:
            contradictions = substrate.find_contradictions(content)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("siphon: find_contradictions failed: %s", e)
            contradictions = []
        if contradictions:
            meta["contradicted"] = True
            meta["contradiction_count"] = len(contradictions)
            confidence = round(min(confidence, _CONTRADICTION_CONF_CAP), 3)

        entry = substrate.write_claim(
            content=content,
            category=candidate.get("category", SIPHON_CATEGORY),
            confidence=confidence,
            source=candidate["source"],
            verified=False,
            metadata=meta,
        )
        return getattr(entry, "memory_id", None)
    except Exception as e:  # never break the request path
        logger.warning("siphon_interaction failed for %s: %s", capability, e)
        try:
            from src.swarm.integrations import audit_capability
            audit_capability(
                "mcp_memory_siphon", target=capability, status="error",
                detail=str(e)[:300], source=(params or {}).get("source"),
            )
        except Exception:
            pass
        return None
