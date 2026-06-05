import os
import secrets
import logging
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from src.web.branding import PRODUCT_NAME

logger = logging.getLogger(__name__)

app = FastAPI(title=PRODUCT_NAME, docs_url=None, redoc_url=None)

_session_secret = os.environ.get("SESSION_SECRET") or secrets.token_hex(32)
app.add_middleware(SessionMiddleware, secret_key=_session_secret)

_is_production = os.environ.get("REPLIT_DEPLOYMENT") == "1"


@app.middleware("http")
async def _no_cache_static(request: Request, call_next):
    response = await call_next(request)
    if not _is_production and request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


app.mount("/static", StaticFiles(directory="static"), name="static")

from src.web.auth import router as auth_router
from src.web.routes_keys import router as keys_router
from src.web.routes_services import router as services_router
from src.web.routes_tasks import router as tasks_router
from src.web.routes_governance import router as governance_router

app.include_router(auth_router)
app.include_router(keys_router)
app.include_router(services_router)
app.include_router(tasks_router)
app.include_router(governance_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.on_event("startup")
async def startup():
    try:
        from src.database import init_db
        init_db()
        logger.info("Database initialized")
    except Exception as e:
        logger.error("Database init failed: %s", e)

    try:
        from src.web.routes_keys import sync_keys_to_env
        sync_keys_to_env()
        logger.info("API keys synced to environment")
    except Exception as e:
        logger.warning("Failed to sync API keys: %s", e)

    # Boot handshake (env_1 -> env_2): validate provider config and that every
    # required secret is present. A failure here is intentionally fatal so the
    # app never serves traffic in a misconfigured state.
    from src.providers.handshake import run_handshake
    run_handshake()
