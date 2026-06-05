import os
import secrets
import logging
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from src.web.branding import PRODUCT_NAME
from src.web.templating import templates

logger = logging.getLogger(__name__)

app = FastAPI(title=PRODUCT_NAME, docs_url=None, redoc_url=None)

_REDIRECT_CODES = {301, 302, 303, 307, 308}

_ERROR_TITLES = {
    404: "Page not found",
    403: "Forbidden",
    429: "Too many requests",
    500: "Something went wrong",
}


def _render_error(request: Request, status_code: int, message: str):
    title = _ERROR_TITLES.get(status_code, "Error")
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "status_code": status_code,
            "title": title,
            "message": message,
        },
        status_code=status_code,
    )


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
    # Preserve redirect-style exceptions (e.g. require_auth -> /login).
    location = (exc.headers or {}).get("Location") if exc.headers else None
    if exc.status_code in _REDIRECT_CODES and location:
        return RedirectResponse(url=location, status_code=exc.status_code)
    # Use the provided detail only for client errors; never echo it for 5xx.
    if exc.status_code >= 500:
        message = "An unexpected error occurred. Please try again later."
    else:
        message = exc.detail if isinstance(exc.detail, str) else _ERROR_TITLES.get(exc.status_code, "Request error")
    return _render_error(request, exc.status_code, message)


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(request: Request, exc: RequestValidationError):
    # Malformed/missing fields: respond cleanly without echoing the raw payload.
    return _render_error(request, 400, "The request was malformed or missing required fields.")


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    # Log the full traceback server-side; never leak it to the client.
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return _render_error(
        request,
        500,
        "An unexpected error occurred. Please try again later.",
    )

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
from src.web.routes_capabilities import router as capabilities_router
from src.web.routes_subscriptions import router as subscriptions_router

app.include_router(auth_router)
app.include_router(keys_router)
app.include_router(services_router)
app.include_router(tasks_router)
app.include_router(governance_router)
app.include_router(capabilities_router)
app.include_router(subscriptions_router)


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

    try:
        from src.web.settings_store import sync_settings_to_env
        sync_settings_to_env()
        logger.info("App settings synced to environment")
    except Exception as e:
        logger.warning("Failed to sync app settings: %s", e)

    # Boot handshake (env_1 -> env_2): validate provider config and that every
    # required secret is present. A failure here is intentionally fatal so the
    # app never serves traffic in a misconfigured state.
    from src.providers.handshake import run_handshake
    run_handshake()

    # Browser capability handshake — validates backend config and any required
    # backend secrets. With the default empty required list it simply boots the
    # capability disabled until credentials are added.
    from src.browser.handshake import run_browser_handshake
    run_browser_handshake()
