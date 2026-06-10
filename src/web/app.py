import os
import secrets
import logging
import pathlib
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from src.web.branding import PRODUCT_NAME

logger = logging.getLogger(__name__)

app = FastAPI(title=PRODUCT_NAME, docs_url=None, redoc_url=None)

_REDIRECT_CODES = {301, 302, 303, 307, 308}
_SPA_INDEX = pathlib.Path("static/spa/index.html")


def _is_api_request(request: Request) -> bool:
    return request.url.path.startswith("/api/") or request.url.path == "/api"


def _error_response(request: Request, status_code: int, detail: str):
    if _is_api_request(request):
        return JSONResponse({"detail": detail}, status_code=status_code)
    # For browser navigation, serve the SPA which renders its own error UI
    if _SPA_INDEX.is_file():
        return FileResponse(_SPA_INDEX, status_code=200)
    return JSONResponse({"detail": detail}, status_code=status_code)


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
    location = (exc.headers or {}).get("Location") if exc.headers else None
    if exc.status_code in _REDIRECT_CODES and location:
        return RedirectResponse(url=location, status_code=exc.status_code)
    if exc.status_code >= 500:
        detail = "An unexpected error occurred. Please try again later."
    else:
        detail = exc.detail if isinstance(exc.detail, str) else "Request error"
    return _error_response(request, exc.status_code, detail)


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(request: Request, exc: RequestValidationError):
    return _error_response(request, 400, "The request was malformed or missing required fields.")


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return _error_response(request, 500, "An unexpected error occurred. Please try again later.")


_session_secret = os.environ.get("SESSION_SECRET") or secrets.token_hex(32)
app.add_middleware(SessionMiddleware, secret_key=_session_secret)

_is_production = os.environ.get("REPLIT_DEPLOYMENT") == "1"


@app.middleware("http")
async def _cache_headers(request: Request, call_next):
    response = await call_next(request)
    if not _is_production and request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


app.mount("/static", StaticFiles(directory="static"), name="static")

from src.web.api.router import router as api_router  # noqa: E402

app.include_router(api_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


# SPA fallback — all non-API, non-static paths serve the React SPA index.
# Registered last so /api/* and /static/* mounts take priority.
@app.get("/{full_path:path}")
async def _spa_fallback(full_path: str):
    if _SPA_INDEX.is_file():
        return FileResponse(_SPA_INDEX)
    return JSONResponse({"detail": "Frontend not built. Run: cd frontend && npm run build"}, status_code=404)


def _ensure_spa_built():
    """Build the React SPA if the output directory is missing (e.g. clean deployment clone)."""
    if _SPA_INDEX.is_file():
        return
    build_script = pathlib.Path("scripts/build-frontend.sh")
    if not build_script.is_file():
        logger.warning("SPA not built and build script not found — frontend will be unavailable")
        return
    import subprocess
    logger.info("static/spa/index.html not found — running frontend build…")
    try:
        result = subprocess.run(
            ["bash", str(build_script)],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            logger.info("Frontend build complete")
        else:
            logger.error("Frontend build failed:\n%s\n%s", result.stdout, result.stderr)
    except Exception as e:
        logger.error("Frontend build error: %s", e)


@app.on_event("startup")
async def startup():
    _ensure_spa_built()

    try:
        from src.database import init_db
        init_db()
        logger.info("Database initialized")
    except Exception as e:
        logger.error("Database init failed: %s", e)

    try:
        from src.promptopt import store as prompt_store
        from src.promptopt import service as prompt_service
        prompt_store.seed_default_programs()
        recovered = prompt_service.recover_orphans()
        logger.info("Prompt engine seeded; %d orphaned run(s) recovered", recovered)
    except Exception as e:
        logger.warning("Prompt engine init failed: %s", e)

    try:
        from src.web.routes_keys import sync_keys_to_env
        sync_keys_to_env()
        logger.info("API keys synced to environment")
    except Exception as e:
        logger.warning("Failed to sync API keys: %s", e)

    try:
        from src.web.settings_store import sync_settings_to_env
        sync_settings_to_env()
        logger.info("App settings synced to environment")
    except Exception as e:
        logger.warning("Failed to sync app settings: %s", e)

    try:
        from src.providers.local_registry import load_local_models
        local_models = load_local_models()
        logger.info("Local model registry: %d local model(s) available", len(local_models))
    except Exception as e:
        logger.warning("Local model registry load failed: %s", e)

    from src.providers.handshake import run_handshake
    run_handshake()

    from src.browser.handshake import run_browser_handshake
    run_browser_handshake()

    try:
        from src.finance.scheduler import get_scheduler
        import os as _os
        interval = int(_os.environ.get("FINANCE_SYNC_INTERVAL", "3600") or "3600")
        await get_scheduler().start(interval=interval)
        logger.info("Finance scheduler started (opt-in via ENABLE_FINANCE_AUTOSYNC)")
    except Exception as e:
        logger.warning("Finance scheduler failed to start: %s", e)

    try:
        from src.coach.scheduler import get_scheduler as get_coach_scheduler
        import os as _os
        coach_interval = int(_os.environ.get("COACH_REFLECT_INTERVAL", "21600") or "21600")
        await get_coach_scheduler().start(interval=coach_interval)
        logger.info("Coach scheduler started (opt-in via ENABLE_COACH_REFLECTION)")
    except Exception as e:
        logger.warning("Coach scheduler failed to start: %s", e)
