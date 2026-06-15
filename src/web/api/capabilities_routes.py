"""Capability matrix API routes.

JSON endpoints for the unified capability layer (v2). The React SPA uses
these to render the full capability matrix — every category, its active
provider, secret status, jurisdiction locus, and live health.

Swap self-test: ``GET /api/capabilities/swap-test?category=database&key=postgresql``
proves a config-only provider swap takes effect with zero code change.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from src.web.auth import require_auth

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/capabilities/matrix")
async def api_capability_matrix(_=Depends(require_auth)):
    """Return the full capability matrix for all categories.

    Shape::

        {
          "matrix": {
            "database": {
              "providers": [{...}],
              "active_key": "postgresql",
              "enabled_count": 1,
              "available_count": 1,
            },
            "llm": {...},
            ...
          },
          "categories": ["llm", "browser", "database", ...],
        }
    """
    try:
        from src.capabilities.resolver import get_resolver
        from src.capabilities.config import ALL_CATEGORIES
        resolver = get_resolver()
        matrix = resolver.capability_matrix()
        return {"matrix": matrix, "categories": ALL_CATEGORIES}
    except Exception as exc:
        logger.exception("capability matrix failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/capabilities/category/{category}")
async def api_capability_category(category: str, _=Depends(require_auth)):
    """Return the status for a single capability category."""
    from src.capabilities.config import ALL_CATEGORIES
    if category not in ALL_CATEGORIES:
        raise HTTPException(
            status_code=404,
            detail="Unknown capability category '%s'. Valid: %s" % (
                category, ", ".join(ALL_CATEGORIES)
            ),
        )
    try:
        from src.capabilities.resolver import get_resolver
        resolver = get_resolver()
        matrix = resolver.capability_matrix()
        return {"category": category, "status": matrix.get(category, {})}
    except Exception as exc:
        logger.exception("capability category status failed for %s", category)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/capabilities/swap-test")
async def api_capability_swap_test(
    category: str = Query(..., description="Capability category"),
    key: str = Query(..., description="Provider/backend key to test"),
    _=Depends(require_auth),
):
    """Prove a config-only provider swap takes effect with zero code change.

    Returns ``{"ok": bool, "resolved_key": str|null, "detail": str}``.
    """
    from src.capabilities.config import ALL_CATEGORIES
    if category not in ALL_CATEGORIES:
        raise HTTPException(
            status_code=404,
            detail="Unknown capability category '%s'." % category,
        )
    try:
        from src.capabilities.resolver import get_resolver
        resolver = get_resolver()
        result = resolver.swap_self_test(category, key)
        return result
    except Exception as exc:
        logger.exception("swap self-test failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/capabilities/reload")
async def api_capability_reload(_=Depends(require_auth)):
    """Force re-evaluation of the capability registry (after a config change).

    Clears the availability cache and reloads the env_1 config from disk so
    a provider swap takes effect without a cold restart.
    """
    try:
        from src.capabilities.resolver import get_resolver
        resolver = get_resolver()
        resolver.reload()
        return {"ok": True, "detail": "Capability registry reloaded."}
    except Exception as exc:
        logger.exception("capability reload failed")
        raise HTTPException(status_code=500, detail=str(exc))
