import logging

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse

from src.database import SessionLocal
from src.models import ApiKey
from src.web.auth import require_auth
from src.web.service_catalog import (
    SERVICE_CATALOG,
    manual_services,
    connector_services,
)
from src.web.connectors import get_connection_status, proxy_available

logger = logging.getLogger(__name__)
router = APIRouter()
from src.web.templating import templates


def _saved_providers():
    db = SessionLocal()
    try:
        return {k.provider for k in db.query(ApiKey).all()}
    finally:
        db.close()


@router.get("/directory", response_class=HTMLResponse)
async def directory_page(request: Request, _=Depends(require_auth)):
    saved = _saved_providers()
    services = []
    for svc in manual_services():
        services.append({**svc, "saved": svc["slug"] in saved})

    # Group by category for display.
    grouped = {}
    for svc in services:
        grouped.setdefault(svc["category"], []).append(svc)
    grouped_list = sorted(grouped.items(), key=lambda kv: kv[0])

    return templates.TemplateResponse("directory.html", {
        "request": request,
        "grouped": grouped_list,
        "msg": request.query_params.get("msg"),
        "err": request.query_params.get("err"),
    })


@router.get("/connected", response_class=HTMLResponse)
async def connected_page(request: Request, _=Depends(require_auth)):
    services = connector_services()
    names = [s["connector"] for s in services]
    status = get_connection_status(names) if proxy_available() else {}

    rows = []
    for svc in services:
        st = status.get(svc["connector"], {})
        rows.append({
            "name": svc["name"],
            "category": svc["category"],
            "connector": svc["connector"],
            "connected": bool(st.get("connected")),
            "created_at": st.get("created_at"),
        })
    rows.sort(key=lambda r: (not r["connected"], r["category"], r["name"]))
    connected_count = sum(1 for r in rows if r["connected"])

    return templates.TemplateResponse("connected.html", {
        "request": request,
        "rows": rows,
        "proxy_available": proxy_available(),
        "connected_count": connected_count,
        "total_count": len(rows),
    })
