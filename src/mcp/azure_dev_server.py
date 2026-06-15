"""Azure MCP Dev Accelerator — a governed MCP server (streamable HTTP).

Exposes the deployed MyDude Azure stack (the private ``mydude`` resource group)
to MCP clients as governed tools:

  * ``azure_cosmos_read``    — read-only Cosmos DB SQL query
  * ``azure_pg_select``      — SELECT-only query against an allow-listed Postgres db
  * ``azure_deploy_status``  — live ARM deployment state + non-secret outputs
  * ``azure_aoai_complete``  — GOVERNED completion via the MyDude swarm (no raw AOAI)
  * ``azure_deploy_plan``    — PLAN phase (ARM what-if; no resources, no cost)
  * ``azure_deploy_apply``   — APPLY phase (BILLABLE; broker-gated two-phase confirm)
  * ``memory_recall``        — read-only recall from MyDude's long-term governed memory

Every successful interaction is additionally siphoned (governed + sanitized) into
that same long-term memory so the brain self-improves from its own headless use.
This is purely additive — it never alters a tool's result and can be turned off
with ``ENABLE_MCP_MEMORY_SIPHON=false``.

Governance (pillars 1-6): every tool dispatches through the SAME
contract → policy → broker → integration → audit pipeline the rest of MyDude
uses (:class:`src.swarm.broker.CapabilityBroker`). There is no bypass and no raw
provider passthrough — the governed-completion tool routes through the swarm
service, and the destructive apply is default-deny behind a signed two-phase
plan token plus an explicit confirm phrase. Provider/credentials are sourced at
runtime (Key Vault / managed identity) and never hardcoded.

Transport: streamable HTTP (stateless JSON) so it scales horizontally as a
VNet-integrated Azure Container App. Because it is reachable over the network,
**bearer-token auth is mandatory and fail-loud**: the expected token is sourced
from Key Vault (or an injected env var the container populates from Key Vault) at
startup; if it is absent the server REFUSES to start. An unauthenticated request
to any path other than the health probe is rejected with 401.

Run locally (after exporting a dev token) ::

    AZURE_MCP_AUTH_TOKEN=dev-only-token python -m src.mcp.azure_dev_server

In the container the token is read from Key Vault; never print it.
"""
import asyncio
import hmac
import json
import logging
import os
from typing import Annotated, Any, Dict, List, Optional

from pydantic import Field

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from src.swarm.broker import CapabilityBroker
from src.swarm.integrations import Integrations
from src.swarm.policy import PolicyEngine

logger = logging.getLogger(__name__)

#: Sentinel distinguishing "argument not passed" from an explicit ``None``.
_UNSET = object()

SERVER_NAME = "mydude-azure-dev-accelerator"

# -- auth configuration -------------------------------------------------------
#: Env var the container populates from Key Vault at startup (preferred fast path).
AZURE_MCP_TOKEN_ENV = "AZURE_MCP_AUTH_TOKEN"
#: Key Vault secret name to fall back to when the env var is unset.
AZURE_MCP_SECRET_NAME_ENV = "AZURE_MCP_AUTH_SECRET_NAME"
DEFAULT_AUTH_SECRET_NAME = "azure-mcp-auth-token"

#: Unauthenticated health-probe path (everything else requires the bearer token).
HEALTH_PATH = "/healthz"
#: Streamable-HTTP MCP endpoint path.
MCP_PATH = "/mcp"

#: Comma-separated Host allow-list for MCP's DNS-rebinding protection (the
#: Container App FQDN behind ingress). When set, protection stays ON.
AZURE_MCP_ALLOWED_HOSTS_ENV = "AZURE_MCP_ALLOWED_HOSTS"
#: Comma-separated Origin allow-list (optional).
AZURE_MCP_ALLOWED_ORIGINS_ENV = "AZURE_MCP_ALLOWED_ORIGINS"
#: Explicit opt-out of MCP's host check for ingress-fronted deploys where the
#: platform (Container Apps) already validates Host. Truthy = protection OFF.
AZURE_MCP_DISABLE_HOST_CHECK_ENV = "AZURE_MCP_DISABLE_HOST_CHECK"

#: Master switch for the additive write-back siphon that distills every
#: successful interaction into long-term governed memory. ON by default.
ENABLE_MCP_MEMORY_SIPHON_ENV = "ENABLE_MCP_MEMORY_SIPHON"


def _csv_env(name: str) -> List[str]:
    return [p.strip() for p in (os.environ.get(name) or "").split(",") if p.strip()]


def _truthy(val: Optional[str]) -> bool:
    return (val or "").strip().lower() in {"1", "true", "yes", "on"}


def transport_security_from_env():
    """Build MCP DNS-rebinding (Host/Origin) settings from the environment.

    - If an allow-list is configured, protection stays ON and only those hosts/
      origins pass (the correct production posture: set the Container App FQDN).
    - Else if the operator explicitly opts out (ingress already validates Host),
      protection is turned OFF.
    - Otherwise the SDK default (protection ON, localhost only) applies, which is
      what local dev wants. Returns ``None`` in that last case so the SDK default
      is used unchanged.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    hosts = _csv_env(AZURE_MCP_ALLOWED_HOSTS_ENV)
    origins = _csv_env(AZURE_MCP_ALLOWED_ORIGINS_ENV)
    if hosts or origins:
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts or ["*"],
            allowed_origins=origins or ["*"],
        )
    if _truthy(os.environ.get(AZURE_MCP_DISABLE_HOST_CHECK_ENV)):
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    return None


class AzureMcpAuthError(RuntimeError):
    """Raised when the server's auth token cannot be sourced — fail loud."""


def load_expected_token(kv_getter=None) -> str:
    """Source the bearer token the server will require, fail-loud if absent.

    Order: the ``AZURE_MCP_AUTH_TOKEN`` env var first (the container injects it
    from Key Vault for a fast, dependency-free start), then a direct Key Vault
    read of the configured secret name. If neither yields a non-empty value the
    server must NOT start unauthenticated — we raise.

    Args:
        kv_getter: optional ``(secret_name) -> Optional[str]`` override for tests
            (so the auth path is hermetically testable without Azure).
    """
    env_token = (os.environ.get(AZURE_MCP_TOKEN_ENV) or "").strip()
    if env_token:
        return env_token

    secret_name = (os.environ.get(AZURE_MCP_SECRET_NAME_ENV) or DEFAULT_AUTH_SECRET_NAME).strip()
    getter = kv_getter
    if getter is None:
        try:
            from infra.mydude.local.azure_common import kv_get_secret as getter  # type: ignore
        except Exception as e:  # noqa: BLE001 - fail loud below with guidance
            raise AzureMcpAuthError(
                "Cannot load the MCP auth token: neither %s is set nor is Key Vault "
                "reachable (%s). Refusing to start an unauthenticated HTTP MCP server."
                % (AZURE_MCP_TOKEN_ENV, str(e)[:200])
            ) from e
    try:
        kv_token = (getter(secret_name) or "").strip()
    except Exception as e:  # noqa: BLE001
        raise AzureMcpAuthError(
            "Cannot read Key Vault secret '%s' for the MCP auth token (%s). "
            "Set %s or provision the secret. Refusing to start unauthenticated."
            % (secret_name, str(e)[:200], AZURE_MCP_TOKEN_ENV)
        ) from e
    if not kv_token:
        raise AzureMcpAuthError(
            "No MCP auth token available (%s unset and Key Vault secret '%s' "
            "missing/empty). Refusing to start an unauthenticated HTTP MCP server."
            % (AZURE_MCP_TOKEN_ENV, secret_name)
        )
    return kv_token


def _token_matches(presented: str, expected: str) -> bool:
    """Constant-time bearer-token comparison (HMAC compare; no early-out leak)."""
    if not presented or not expected:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


# -- broker singleton ---------------------------------------------------------

_BROKER: Optional[CapabilityBroker] = None


def get_broker() -> CapabilityBroker:
    """Return the process-wide governed broker (lazy; reused across requests)."""
    global _BROKER
    if _BROKER is None:
        _BROKER = CapabilityBroker(PolicyEngine(), Integrations())
    return _BROKER


def _siphon_enabled() -> bool:
    """Interaction siphon is ON by default; disable with a falsy env value."""
    raw = os.environ.get(ENABLE_MCP_MEMORY_SIPHON_ENV)
    if raw is None or not raw.strip():
        return True
    return _truthy(raw)


async def _maybe_siphon(capability: str, params: Dict[str, Any], data: Any) -> None:
    """Best-effort governed siphon of one interaction into long-term memory.

    Purely additive: it runs the synchronous substrate write off the event loop,
    never alters ``data``, and swallows + audits any failure so the capability's
    own result is never affected. ``memory_*`` capabilities are skipped upstream
    (no recall->write loop)."""
    if not _siphon_enabled() or not capability or capability.startswith("memory_"):
        return
    try:
        from src.memory.siphon import siphon_interaction
        await asyncio.to_thread(siphon_interaction, capability, params, data)
    except Exception as e:  # never break the request path
        logger.warning("MCP memory siphon failed for %s: %s", capability, e)
        try:
            from src.swarm.integrations import audit_capability
            audit_capability("mcp_memory_siphon", target=capability,
                             status="error", detail=str(e)[:300],
                             source=(params or {}).get("source"))
        except Exception:
            pass


async def _dispatch(capability: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Run a capability through the governed broker and return structured output.

    Governance blocks (contract/policy) and honest execution failures are both
    surfaced as ``ValueError`` so the MCP client sees an actionable, sanitized
    message (the integration layer already truncates/sanitizes errors and never
    leaks secrets). A successful call returns the parsed structured payload.
    """
    res = await get_broker().request(capability, params)
    if not res.ok:
        # Contract violation or policy denial — caller-fixable; surface the reason.
        raise ValueError(res.decision.reason or "Blocked by governance policy.")
    if not res.output:
        raise RuntimeError("The capability returned no output.")
    try:
        data = json.loads(res.output)
    except (TypeError, ValueError):
        raise RuntimeError("The capability returned malformed output.")
    if isinstance(data, dict) and data.get("ok") is False:
        # Honest, sanitized failure (e.g. Azure unreachable, plan drift) — fail loud.
        raise ValueError(str(data.get("error") or "The capability failed."))
    # Purely additive: distill this successful interaction into long-term memory
    # so the brain self-improves. Never alters `data`; never raises (best-effort).
    await _maybe_siphon(capability, params, data)
    return data


# -- MCP server + tools -------------------------------------------------------

mcp = FastMCP(
    SERVER_NAME,
    instructions=(
        "Governed access to the MyDude Azure stack (private 'mydude' resource "
        "group). Read Cosmos / Postgres (SELECT-only) / deployment status, get a "
        "GOVERNED completion (no raw Azure OpenAI passthrough), and trigger a "
        "two-phase deployment (plan -> apply). Every tool passes MyDude's "
        "governance pipeline (contract, policy, broker, audit). The destructive "
        "apply is default-deny and requires a signed plan token + exact confirm "
        "phrase obtained from azure_deploy_plan. Use memory_recall to read from "
        "MyDude's long-term governed memory; every successful interaction here is "
        "also siphoned back into that memory (governed + sanitized) so the system "
        "self-improves over time."
    ),
    stateless_http=True,
    json_response=True,
)


@mcp.tool(
    name="azure_cosmos_read",
    title="Read Cosmos DB (read-only)",
    description=(
        "Run a single read-only Cosmos DB SQL query against a container in the "
        "MyDude Azure stack and return bounded, JSON-safe items. Only SELECT "
        "queries are accepted; comments and multiple statements are rejected."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True,
    ),
    structured_output=True,
)
async def azure_cosmos_read_tool(
    database: Annotated[str, Field(description="Cosmos database id.", min_length=1)],
    container: Annotated[str, Field(description="Cosmos container id.", min_length=1)],
    query: Annotated[str, Field(description="A single read-only SELECT query.", min_length=1)],
    parameters: Annotated[
        Optional[List[Dict[str, Any]]],
        Field(description="Optional Cosmos parameter bindings: [{name, value}, ...]."),
    ] = None,
    max_items: Annotated[
        Optional[int],
        Field(description="Max items to return (bounded server-side).", ge=1),
    ] = None,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "database": database, "container": container, "query": query,
        "source": "mcp:azure_cosmos_read",
    }
    if parameters is not None:
        params["parameters"] = parameters
    if max_items is not None:
        params["max_items"] = max_items
    return await _dispatch("azure_cosmos_read", params)


@mcp.tool(
    name="azure_pg_select",
    title="Query Postgres (SELECT-only)",
    description=(
        "Execute a single read-only SELECT/WITH against an allow-listed MyDude "
        "Azure Postgres database and return bounded rows. DML/DDL, multiple "
        "statements, and comment injection are rejected; the session is also "
        "forced read-only as defense in depth."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True,
    ),
    structured_output=True,
)
async def azure_pg_select_tool(
    db_key: Annotated[str, Field(description="Allow-listed database key.", min_length=1)],
    sql: Annotated[str, Field(description="A single read-only SELECT/WITH statement.", min_length=1)],
    params: Annotated[
        Optional[List[Any]],
        Field(description="Optional positional query parameters for the SELECT."),
    ] = None,
    max_rows: Annotated[
        Optional[int],
        Field(description="Max rows to return (bounded server-side).", ge=1),
    ] = None,
) -> Dict[str, Any]:
    call: Dict[str, Any] = {"db_key": db_key, "sql": sql, "source": "mcp:azure_pg_select"}
    if params is not None:
        call["params"] = params
    if max_rows is not None:
        call["max_rows"] = max_rows
    return await _dispatch("azure_pg_select", call)


@mcp.tool(
    name="azure_deploy_status",
    title="Read deployment status (read-only)",
    description=(
        "Return the live ARM provisioning state of the MyDude Azure stack, an "
        "operation-state summary, any failed operations, and the deployment's "
        "non-secret outputs (endpoints/names). Read-only; no secrets are returned."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True,
    ),
    structured_output=True,
)
async def azure_deploy_status_tool() -> Dict[str, Any]:
    return await _dispatch("azure_deploy_status", {"source": "mcp:azure_deploy_status"})


@mcp.tool(
    name="azure_aoai_complete",
    title="Governed completion (no raw Azure OpenAI)",
    description=(
        "Return a GOVERNED completion produced by MyDude's multi-provider swarm "
        "(compliance scoring, hallucination control, provenance, audit). There is "
        "deliberately NO raw Azure OpenAI passthrough — the full governance "
        "envelope is returned alongside the answer."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True,
    ),
    structured_output=True,
)
async def azure_aoai_complete_tool(
    prompt: Annotated[str, Field(description="The task or question for the governed swarm.", min_length=1)],
    domain: Annotated[str, Field(description="Operator domain hint (jurisdiction/benchmark routing).")] = "general",
    team: Annotated[str, Field(description="Operator team hint (normalized).")] = "default",
) -> Dict[str, Any]:
    return await _dispatch("azure_aoai_complete", {
        "prompt": prompt, "domain": domain, "team": team,
        "source": "mcp:azure_aoai_complete",
    })


@mcp.tool(
    name="azure_deploy_plan",
    title="Plan an Azure deployment (what-if; no cost)",
    description=(
        "PLAN phase of the two-phase deploy: run an ARM what-if for the MyDude "
        "Azure stack. Creates no resources and incurs no cost. Returns the change "
        "set, a plan hash, a short-lived signed plan token, and the exact confirm "
        "phrase. Pass the token + plan hash + confirm phrase to azure_deploy_apply "
        "to actually deploy. Deploy parameters are never returned, only fingerprinted."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True,
    ),
    structured_output=True,
)
async def azure_deploy_plan_tool(
    actor: Annotated[str, Field(description="Who is requesting the plan (for the audit trail).")] = "mcp-client",
) -> Dict[str, Any]:
    return await _dispatch("azure_deploy_plan", {
        "actor": actor, "source": "mcp:azure_deploy_plan",
    })


@mcp.tool(
    name="azure_deploy_apply",
    title="Apply an approved Azure deployment (BILLABLE)",
    description=(
        "APPLY phase of the two-phase deploy: execute the approved ARM deployment. "
        "This is BILLABLE and creates/updates real resources. It is default-deny: "
        "the operator must enable ALLOW_AZURE_DEPLOY, and the call must carry the "
        "plan_token + exact plan_hash from a prior azure_deploy_plan plus the exact "
        "confirm phrase. An expired/tampered token or a plan-hash mismatch is "
        "rejected (re-plan required). Every phase is audited."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True,
    ),
    structured_output=True,
)
async def azure_deploy_apply_tool(
    plan_token: Annotated[str, Field(description="Signed plan token from azure_deploy_plan.", min_length=1)],
    plan_hash: Annotated[str, Field(description="The plan_hash returned by azure_deploy_plan.", min_length=1)],
    confirm: Annotated[str, Field(description="The exact confirm phrase returned by azure_deploy_plan.", min_length=1)],
) -> Dict[str, Any]:
    return await _dispatch("azure_deploy_apply", {
        "plan_token": plan_token, "plan_hash": plan_hash, "confirm": confirm,
        "source": "mcp:azure_deploy_apply",
    })


@mcp.tool(
    name="memory_recall",
    title="Recall from long-term memory (read-only)",
    description=(
        "Recall semantically related entries from MyDude's long-term governed "
        "memory — the accumulated, sanitized knowledge of past interactions. "
        "Read-only: it never mutates memory. Private (local-only) entries are "
        "never returned, and only a stable non-secret projection of each entry "
        "is exposed (id, content, category, confidence, verified, source, time)."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True,
    ),
    structured_output=True,
)
async def memory_recall_tool(
    query: Annotated[str, Field(description="What to recall (semantic query).", min_length=1)],
    top_k: Annotated[
        Optional[int],
        Field(description="Max entries to return (bounded server-side).", ge=1),
    ] = None,
    category: Annotated[
        Optional[str],
        Field(description="Restrict recall to a single memory category."),
    ] = None,
    min_confidence: Annotated[
        Optional[float],
        Field(description="Minimum confidence (0..1) for returned entries.", ge=0.0, le=1.0),
    ] = None,
) -> Dict[str, Any]:
    call: Dict[str, Any] = {"query": query, "source": "mcp:memory_recall"}
    if top_k is not None:
        call["top_k"] = top_k
    if category is not None:
        call["category"] = category
    if min_confidence is not None:
        call["min_confidence"] = min_confidence
    return await _dispatch("memory_recall", call)


# -- ASGI app (auth-wrapped streamable HTTP) ----------------------------------

class _BearerAuthMiddleware:
    """Pure-ASGI middleware enforcing the bearer token on every non-health path.

    The token is compared in constant time. A missing/incorrect token yields a
    401 with ``WWW-Authenticate: Bearer`` and never reaches the MCP app. This is
    the single guarantee that an HTTP-reachable MCP server is not open.
    """

    def __init__(self, app, expected_token: str, *, health_path: str = HEALTH_PATH):
        if not expected_token:
            raise AzureMcpAuthError("Refusing to wrap the MCP app without an auth token.")
        self._app = app
        self._expected = expected_token
        self._health = health_path

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self._app(scope, receive, send)
        if scope.get("path") == self._health:
            return await self._app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        raw = headers.get(b"authorization", b"").decode("latin-1")
        token = raw[7:].strip() if raw[:7].lower() == "bearer " else ""
        if not _token_matches(token, self._expected):
            return await self._reject(send)
        return await self._app(scope, receive, send)

    async def _reject(self, send):
        body = b'{"error":"unauthorized","detail":"valid bearer token required"}'
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b"Bearer"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": body})


def build_asgi_app(expected_token: str, *, server: Optional[FastMCP] = None,
                   transport_security=_UNSET):
    """Build the auth-wrapped streamable-HTTP ASGI app.

    A parent Starlette app adds an unauthenticated ``/healthz`` probe and mounts
    the MCP streamable-HTTP app at ``/`` (so the MCP endpoint is ``/mcp``). The
    MCP app's lifespan (its session manager) is propagated so streaming works.
    The whole thing is then wrapped in the bearer-auth middleware.

    ``transport_security`` overrides the DNS-rebinding (Host/Origin) settings;
    by default they are derived from the environment. It is applied BEFORE the
    session manager is created (the SDK reads it at that point).
    """
    from contextlib import asynccontextmanager

    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    srv = server or mcp
    ts = transport_security_from_env() if transport_security is _UNSET else transport_security
    if ts is not None:
        srv.settings.transport_security = ts
    mcp_app = srv.streamable_http_app()

    @asynccontextmanager
    async def _lifespan(_app):
        # Drive the MCP session manager's lifespan from the parent app.
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    async def _healthz(_request):
        return JSONResponse({"ok": True, "service": SERVER_NAME})

    parent = Starlette(
        routes=[Route(HEALTH_PATH, _healthz), Mount("/", app=mcp_app)],
        lifespan=_lifespan,
    )
    return _BearerAuthMiddleware(parent, expected_token, health_path=HEALTH_PATH)


def main() -> None:
    """Entry point: source the token fail-loud, then serve over streamable HTTP."""
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    token = load_expected_token()  # fail loud if absent — never serve open
    app = build_asgi_app(token)
    host = os.environ.get("AZURE_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("AZURE_MCP_PORT", "8080"))
    logger.info("Starting %s on %s:%s (MCP path %s)", SERVER_NAME, host, port, MCP_PATH)
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level=os.environ.get("LOG_LEVEL", "info").lower())


if __name__ == "__main__":
    main()
