"""Azure dev-accelerator backend adapters (provider-agnostic, hermetically testable).

These are the data-plane adapters the Azure MCP tools reach THROUGH the governed
broker (contract -> policy -> broker -> integration -> audit). The MCP server and
the ``Integrations.azure_*`` handlers never talk to Azure directly; they call
these adapters, which honor the MyDude governance pillars:

  * #1 no placeholders / fail loud — every backend raises :class:`AzureBackendError`
    when its endpoint / credential / driver / DSN is missing. It never returns
    mock data or silently degrades.
  * #2 provider-agnostic — endpoints are read from the live ARM deployment outputs
    via :mod:`infra.mydude.local.azure_common`, never hardcoded at a call site.
  * #3 separate provider from secrets — DSNs and credentials are sourced at runtime
    (env -> Key Vault via ``azure_common``); secret values never leave this layer
    and are never returned to a caller.
  * #4 governed inference — the completion adapter delegates to the governed swarm
    service (:func:`src.swarm.service.run_governed_swarm`); there is NO raw Azure
    OpenAI passthrough here.

Hermetic by construction: NOTHING Azure/DB-specific is imported at module load.
Every Azure SDK / psycopg2 import is lazy, inside a default builder that only runs
when no client/connector/runner was injected. Tests inject fakes and exercise the
entire surface with no network, no Azure SDK, and no database.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from typing import Any, Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AzureBackendError(RuntimeError):
    """Fail-loud error for any Azure backend (missing config/credential/driver,
    connection failure, or provider error). Carries a safe, caller-facing message
    — never a raw secret or full internal stack detail."""


class SqlValidationError(ValueError):
    """Raised when a SQL string is not a single read-only SELECT/WITH statement."""


# ---------------------------------------------------------------------------
# Bounds / allow-lists
# ---------------------------------------------------------------------------

#: The only databases the SELECT-only tool may touch (mirrors azure_common.DB_ROLE).
ALLOWED_PG_DATABASES = ("agents_home", "provider_home")

DEFAULT_PG_MAX_ROWS = 200
PG_HARD_MAX_ROWS = 1000
DEFAULT_COSMOS_MAX_ITEMS = 100
COSMOS_HARD_MAX_ITEMS = 500
MAX_SQL_LEN = 4000

#: Two-phase deploy plan token lifetime. Short by design: a plan must be applied
#: promptly or re-planned, so a stale/forgotten approval cannot be replayed later.
PLAN_TOKEN_TTL_SECONDS = 900  # 15 minutes
_PLAN_TOKEN_SALT = "mydude.azure.deploy.plan.v1"

#: The exact phrase a caller must echo in ``confirm`` to authorize the billable
#: APPLY phase (mirrors the explicit-confirmation pattern of irreversible flows).
#: Single source of truth — imported by BOTH the contract precondition and the
#: deploy handlers so the gate text can never drift between governance layers.
AZURE_DEPLOY_CONFIRM_PHRASE = "APPLY AZURE DEPLOYMENT"

# Keywords that may NEVER appear in a read-only query. Matched as whole words on
# the upper-cased SQL, so identifiers like ``updated_at`` / ``created_by`` /
# ``deleted`` (suffixed/prefixed) do NOT trip them, while a bare ``UPDATE`` does.
# Includes RETURNING + INTO so a data-modifying CTE (``WITH x AS (DELETE ...
# RETURNING ...) SELECT ...``) or ``SELECT ... INTO newtable`` is rejected too.
_FORBIDDEN_SQL_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE",
    "GRANT", "REVOKE", "MERGE", "UPSERT", "REPLACE", "CALL", "EXEC", "EXECUTE",
    "COPY", "VACUUM", "ANALYZE", "REINDEX", "CLUSTER", "REFRESH", "COMMENT",
    "SET", "RESET", "LOCK", "DO", "DECLARE", "FETCH", "MOVE", "LISTEN",
    "NOTIFY", "UNLISTEN", "PREPARE", "DEALLOCATE", "BEGIN", "START", "COMMIT",
    "ROLLBACK", "SAVEPOINT", "RELEASE", "INTO", "RETURNING",
)

_PG_ENV_VAR = {
    "agents_home": "PG_AGENTS_HOME_DSN",
    "provider_home": "PG_PROVIDER_HOME_DSN",
}


# ---------------------------------------------------------------------------
# SQL validators
# ---------------------------------------------------------------------------

import re as _re

_SELECT_OR_WITH = _re.compile(r"^(SELECT|WITH)\b")


def validate_select_only(sql: str) -> str:
    """Validate that ``sql`` is a single read-only SELECT/WITH query.

    Returns the cleaned single-statement SQL on success. Raises
    :class:`SqlValidationError` otherwise. This is a deliberately fail-SAFE gate:
    when in doubt it rejects, because a false rejection is harmless but a false
    acceptance could mutate data.

    Rules:
      1. non-empty, bounded length;
      2. no SQL comment markers (``--`` ``/*`` ``*/`` ``#``) — a classic way to
         smuggle a second intent past a naive parser;
      3. at most one statement: a single optional trailing ``;`` is stripped, and
         no other ``;`` may remain (defeats ``SELECT 1; DROP TABLE t``);
      4. must begin with ``SELECT`` or ``WITH``;
      5. no forbidden DML/DDL keyword anywhere (whole-word match), which also
         catches data-modifying CTEs and ``SELECT ... INTO``.
    """
    if not isinstance(sql, str):
        raise SqlValidationError("SQL query must be a string.")
    cleaned = sql.strip()
    if not cleaned:
        raise SqlValidationError("SQL query is empty.")
    if len(cleaned) > MAX_SQL_LEN:
        raise SqlValidationError("SQL query is too long (max %d characters)." % MAX_SQL_LEN)
    for marker in ("--", "/*", "*/", "#"):
        if marker in cleaned:
            raise SqlValidationError("SQL comments are not permitted.")
    body = cleaned[:-1].rstrip() if cleaned.endswith(";") else cleaned
    if ";" in body:
        raise SqlValidationError("Only a single statement is permitted (no ';').")
    upper = body.upper()
    if not _SELECT_OR_WITH.match(upper):
        raise SqlValidationError("Only read-only SELECT/WITH queries are permitted.")
    for kw in _FORBIDDEN_SQL_KEYWORDS:
        if _re.search(r"\b" + kw + r"\b", upper):
            raise SqlValidationError(
                "Disallowed keyword '%s' — only read-only SELECT/WITH queries are permitted." % kw
            )
    return body


def validate_cosmos_query(query: str) -> str:
    """Validate a Cosmos SQL-API read query.

    Cosmos's SQL dialect is query-only (the data-plane query API cannot mutate),
    but we still enforce a single, comment-free ``SELECT`` so the tool surface is
    uniformly read-only and tamper-resistant.
    """
    if not isinstance(query, str):
        raise SqlValidationError("Cosmos query must be a string.")
    q = query.strip()
    if not q:
        raise SqlValidationError("Cosmos query is empty.")
    if len(q) > MAX_SQL_LEN:
        raise SqlValidationError("Cosmos query is too long (max %d characters)." % MAX_SQL_LEN)
    for marker in ("--", "/*", "*/", ";"):
        if marker in q:
            raise SqlValidationError("Cosmos query must be a single SELECT with no comments or ';'.")
    if not q.upper().startswith("SELECT"):
        raise SqlValidationError("Only SELECT queries are permitted for Cosmos reads.")
    return q


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bound_int(val: Any, default: int, lo: int, hi: int) -> int:
    """Clamp ``val`` to [lo, hi], falling back to ``default`` on bad input."""
    try:
        n = int(val)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _jsonable(v: Any) -> Any:
    """Coerce a DB/Cosmos value into a JSON-serializable form for structured output.

    datetimes, Decimals, UUIDs, bytes and other rich types become strings so the
    MCP structured-output channel never chokes on a non-serializable cell.
    """
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    return str(v)


# ---------------------------------------------------------------------------
# Two-phase deploy plan token (signed, short-lived, tamper-evident)
# ---------------------------------------------------------------------------


#: Key Vault secret NAME (only the name) holding the deploy-token signing secret.
#: Mirrors the bearer-token pattern (pillar #3): the container is handed this name
#: via AZURE_MCP_DEPLOY_SECRET_NAME and fetches the VALUE from Key Vault at runtime
#: under its managed identity — the value is never baked into the image or Bicep.
#: ``infra/mydude/local/setup_mcp_token.py`` mints it.
DEPLOY_TOKEN_SECRET_NAME_DEFAULT = "azure-mcp-deploy-token-secret"


def _load_deploy_token_secret() -> str:
    """Resolve the stable deploy-token signing secret (pillar #3), fail-loud.

    Resolution order: explicit env (``MCP_DEPLOY_TOKEN_SECRET`` / ``SESSION_SECRET``
    — for local/dev) first, then Key Vault by name (``AZURE_MCP_DEPLOY_SECRET_NAME``)
    under the runtime managed identity. We NEVER fall back to an ephemeral
    per-process key: that would silently break the plan->apply binding across
    restarts/replicas instead of failing clearly.
    """
    secret = os.environ.get("MCP_DEPLOY_TOKEN_SECRET") or os.environ.get("SESSION_SECRET")
    if secret:
        return secret
    name = os.environ.get("AZURE_MCP_DEPLOY_SECRET_NAME", DEPLOY_TOKEN_SECRET_NAME_DEFAULT)
    try:
        from infra.mydude.local import azure_common as az

        value = az.kv_get_secret(name)
    except Exception as e:  # noqa: BLE001 - surface as a fail-loud backend error
        raise AzureBackendError(
            "No deploy-token signing secret configured and the Key Vault lookup "
            "failed: %s" % (str(e)[:200])
        ) from e
    if not value:
        raise AzureBackendError(
            "No deploy-token signing secret configured. Set MCP_DEPLOY_TOKEN_SECRET "
            "(or SESSION_SECRET), or store it in Key Vault as '%s' "
            "(run infra/mydude/local/setup_mcp_token.py)." % name
        )
    return value


def _plan_signer():
    """Return the itsdangerous serializer used to sign/verify deploy plan tokens.

    Fail-loud (pillar #1): a stable signing secret MUST be configured (see
    :func:`_load_deploy_token_secret`). We never fall back to an ephemeral
    per-process key, because that would silently break the plan->apply binding
    across restarts/replicas instead of failing clearly.
    """
    from itsdangerous import URLSafeTimedSerializer

    return URLSafeTimedSerializer(_load_deploy_token_secret(), salt=_PLAN_TOKEN_SALT)


def _canonical_hash(obj: Any) -> str:
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_plan_hash(changes: Any) -> str:
    """Stable hash of a what-if change set — the identity the apply phase is bound to."""
    return _canonical_hash(changes)


def compute_params_hash(params: Any) -> str:
    """Stable hash of the deploy parameters (binds apply to identical inputs).

    The hash is one-way: it binds the parameter values (including secret ones)
    without ever exposing them. The params themselves are NEVER returned to a
    caller — only this fingerprint.
    """
    return _canonical_hash(params)


def compute_template_hash(template: Any) -> str:
    """Stable hash of the compiled ARM template (binds apply to identical EFFECTS).

    The plan-hash (what-if change set) only fingerprints which resources change.
    Two different templates can produce the same {change_type, resource_id} set
    while applying different property values, so the approved token ALSO binds the
    exact compiled template — together (template_hash + params_hash) they pin the
    byte-for-byte deployment payload the apply phase is allowed to submit.
    """
    return _canonical_hash(template)


def sign_plan_token(
    *,
    plan_hash: str,
    params_hash: str,
    template_hash: Optional[str] = None,
    actor: Optional[str] = None,
    source: Optional[str] = None,
    nonce: Optional[str] = None,
) -> str:
    """Mint a short-lived signed token for an approved deploy plan.

    ``template_hash`` binds the EXACT compiled template that was reviewed at plan
    time (see :func:`compute_template_hash`). The apply phase refuses if it is
    absent or no longer matches, so a stale token can never authorize a changed
    deployment that happens to touch the same resource IDs.

    Fail-loud (pillar #1/#4): ALL THREE of plan/params/template hash are required.
    A token minted without the complete binding could later authorize an apply
    whose parameters (which carry deployment effects AND secrets) were never pinned,
    so we refuse to issue such a token at all.
    """
    if not plan_hash or not params_hash or not template_hash:
        raise AzureBackendError(
            "Refusing to sign a deploy plan token without a complete binding "
            "(plan_hash + params_hash + template_hash all required)."
        )
    payload = {
        "plan_hash": plan_hash,
        "params_hash": params_hash,
        "template_hash": template_hash,
        "actor": actor,
        "source": source,
        "nonce": nonce or secrets.token_hex(8),
        "issued_at": int(time.time()),
    }
    return _plan_signer().dumps(payload)


def verify_plan_token(token: str, *, max_age: int = PLAN_TOKEN_TTL_SECONDS) -> Dict[str, Any]:
    """Verify + decode a deploy plan token. Fail-loud on tamper/expiry."""
    from itsdangerous import BadSignature, SignatureExpired

    if not token or not isinstance(token, str):
        raise AzureBackendError("A deploy plan token is required.")
    try:
        return _plan_signer().loads(token, max_age=max_age)
    except SignatureExpired as e:
        raise AzureBackendError(
            "Deploy plan token has expired. Run azure_deploy_plan again to get a fresh approval."
        ) from e
    except BadSignature as e:
        raise AzureBackendError("Deploy plan token is invalid or has been tampered with.") from e


# ---------------------------------------------------------------------------
# Backend adapter
# ---------------------------------------------------------------------------


class AzureBackends:
    """Adapter over the deployed MyDude Azure stack's data + control plane.

    Every collaborator is injectable so the whole surface is hermetically
    testable. When a collaborator is not injected, a fail-loud default builder
    constructs the real client lazily from :mod:`infra.mydude.local.azure_common`.

    Args:
        cosmos_client: object exposing ``get_database_client(db).get_container_client(c)``.
        pg_connector: callable ``(db_key) -> DB-API connection``.
        deployments_client: object exposing ``deployments`` + ``deployment_operations``.
        swarm_runner: async ``(prompt, domain, team, check_providers) -> dict`` (governed).
        deploy_planner: callable ``() -> plan dict`` (what-if). Overrides the default.
        deploy_applier: callable ``(expected_params_hash, no_wait) -> dict``.
    """

    def __init__(
        self,
        *,
        cosmos_client: Any = None,
        pg_connector: Optional[Callable[[str], Any]] = None,
        deployments_client: Any = None,
        swarm_runner: Optional[Callable[..., Any]] = None,
        deploy_planner: Optional[Callable[[], Dict[str, Any]]] = None,
        deploy_applier: Optional[Callable[..., Dict[str, Any]]] = None,
    ) -> None:
        self._cosmos_client = cosmos_client
        self._pg_connector = pg_connector
        self._deployments_client = deployments_client
        self._swarm_runner = swarm_runner
        self._deploy_planner = deploy_planner
        self._deploy_applier = deploy_applier

    # -- default client builders (lazy, fail-loud) ------------------------

    def _cosmos(self):
        if self._cosmos_client is not None:
            return self._cosmos_client
        try:
            from azure.cosmos import CosmosClient
        except ImportError as e:  # pragma: no cover - dependency declared
            raise AzureBackendError("azure-cosmos is not installed.") from e
        from infra.mydude.local import azure_common as az
        try:
            endpoint = az.cosmos_endpoint()
        except az.AzureWiringError as e:
            raise AzureBackendError(str(e)) from e
        return CosmosClient(url=endpoint, credential=az.credential())

    def _pg_conn(self, db_key: str):
        if self._pg_connector is not None:
            return self._pg_connector(db_key)
        try:
            import psycopg2
        except ImportError as e:  # pragma: no cover - dependency declared
            raise AzureBackendError("psycopg2 is not installed.") from e
        from infra.mydude.local import azure_common as az
        dsn = os.environ.get(_PG_ENV_VAR[db_key], "")
        if not dsn:
            try:
                dsn = az.build_db_dsn(db_key)
            except az.AzureWiringError as e:
                raise AzureBackendError("No DSN available for '%s': %s" % (db_key, e)) from e
        try:
            return psycopg2.connect(dsn, connect_timeout=10)
        except Exception as e:  # noqa: BLE001
            raise AzureBackendError(
                "Postgres connection failed for '%s': %s" % (db_key, str(e)[:200])
            ) from e

    def _deployments(self):
        if self._deployments_client is not None:
            return self._deployments_client
        try:
            from azure.mgmt.resource.deployments import DeploymentsMgmtClient
        except ImportError as e:  # pragma: no cover - dependency declared
            raise AzureBackendError("azure-mgmt-resource-deployments is not installed.") from e
        from infra.mydude.local import azure_common as az
        try:
            return DeploymentsMgmtClient(az.credential(), az.subscription_id())
        except az.AzureWiringError as e:
            raise AzureBackendError(str(e)) from e

    # -- Cosmos read ------------------------------------------------------

    def cosmos_read(
        self,
        database: str,
        container: str,
        query: str,
        parameters: Optional[List[Dict[str, Any]]] = None,
        max_items: int = DEFAULT_COSMOS_MAX_ITEMS,
    ) -> Dict[str, Any]:
        """Run a read-only Cosmos SQL query against a container; return bounded items."""
        if not database or not container:
            raise AzureBackendError("Both 'database' and 'container' are required.")
        q = validate_cosmos_query(query)
        limit = _bound_int(max_items, DEFAULT_COSMOS_MAX_ITEMS, 1, COSMOS_HARD_MAX_ITEMS)
        client = self._cosmos()
        try:
            db = client.get_database_client(database)
            cont = db.get_container_client(container)
            iterator = cont.query_items(
                query=q,
                parameters=list(parameters or []),
                enable_cross_partition_query=True,
                max_item_count=limit,
            )
            out: List[Any] = []
            for item in iterator:
                out.append(_jsonable(item))
                if len(out) >= limit:
                    break
        except AzureBackendError:
            raise
        except Exception as e:  # noqa: BLE001
            raise AzureBackendError("Cosmos read failed: %s" % (str(e)[:300])) from e
        return {"items": out, "count": len(out), "truncated": len(out) >= limit}

    # -- Postgres SELECT-only --------------------------------------------

    def pg_select(
        self,
        db_key: str,
        sql: str,
        params: Optional[List[Any]] = None,
        max_rows: int = DEFAULT_PG_MAX_ROWS,
    ) -> Dict[str, Any]:
        """Execute a validated read-only SELECT and return bounded, JSON-safe rows.

        Defense in depth: the statement is validated by :func:`validate_select_only`
        AND the session is put in read-only mode, so even a validator gap cannot
        mutate data.
        """
        if db_key not in ALLOWED_PG_DATABASES:
            raise AzureBackendError(
                "Unknown database '%s'. Allowed: %s" % (db_key, ", ".join(ALLOWED_PG_DATABASES))
            )
        safe_sql = validate_select_only(sql)
        limit = _bound_int(max_rows, DEFAULT_PG_MAX_ROWS, 1, PG_HARD_MAX_ROWS)
        conn = self._pg_conn(db_key)
        try:
            try:
                conn.set_session(readonly=True)
            except AttributeError:
                pass  # injected fake connection without session control
            cur = conn.cursor()
            try:
                cur.execute(safe_sql, params or None)
                cols = [d[0] for d in cur.description] if cur.description else []
                fetched = cur.fetchmany(limit + 1)
            finally:
                try:
                    cur.close()
                except Exception:  # noqa: BLE001
                    pass
            truncated = len(fetched) > limit
            rows = [[_jsonable(c) for c in r] for r in fetched[:limit]]
        except AzureBackendError:
            raise
        except Exception as e:  # noqa: BLE001
            raise AzureBackendError("Postgres query failed: %s" % (str(e)[:300])) from e
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        return {"columns": cols, "rows": rows, "rowcount": len(rows), "truncated": truncated}

    # -- Deployment status (read-only) -----------------------------------

    def deploy_status(self) -> Dict[str, Any]:
        """Return the live ARM deployment state + non-secret outputs + op summary."""
        from infra.mydude.local import azure_common as az

        dmc = self._deployments()
        try:
            dep = dmc.deployments.get(az.RG_NAME, az.DEPLOYMENT_NAME)
        except Exception as e:  # noqa: BLE001
            raise AzureBackendError("Could not read deployment status: %s" % (str(e)[:300])) from e
        props = getattr(dep, "properties", None)
        state = getattr(props, "provisioning_state", None)
        raw_outputs = getattr(props, "outputs", None) or {}
        outputs = {
            k: (spec.get("value") if isinstance(spec, dict) else spec)
            for k, spec in raw_outputs.items()
        }
        counts: Dict[str, int] = {}
        failed: List[Dict[str, Any]] = []
        try:
            for op in dmc.deployment_operations.list(az.RG_NAME, az.DEPLOYMENT_NAME):
                p = getattr(op, "properties", None)
                st = getattr(p, "provisioning_state", None) or "?"
                counts[st] = counts.get(st, 0) + 1
                if st == "Failed":
                    tr = getattr(p, "target_resource", None)
                    failed.append({
                        "resource_id": getattr(tr, "id", "?") if tr else "?",
                        "message": getattr(p, "status_message", None),
                    })
        except Exception:  # noqa: BLE001
            pass  # operation breakdown is best-effort detail
        return _jsonable({
            "deployment": az.DEPLOYMENT_NAME,
            "resource_group": az.RG_NAME,
            "state": state,
            "operation_states": counts,
            "failed": failed,
            "outputs": outputs,
        })

    # -- Governed completion (NO raw AOAI passthrough) -------------------

    async def aoai_complete(
        self, prompt: str, domain: str = "general", team: str = "default"
    ) -> Dict[str, Any]:
        """Return a GOVERNED completion (full envelope) — never raw model output.

        Delegates to the same governed swarm service the web app + the original
        MCP tool use, so compliance/hallucination/provenance/audit are applied
        identically (pillar #4). There is deliberately no direct Azure OpenAI call.
        """
        runner = self._swarm_runner
        if runner is None:
            from src.swarm.service import run_governed_swarm
            runner = run_governed_swarm
        return await runner(prompt, domain=domain, team=team, check_providers=True)

    # -- Two-phase deploy (broker-gated; token logic lives in Integrations) --

    def deploy_what_if(self) -> Dict[str, Any]:
        """Execute a what-if (the PLAN phase). Creates no resources, incurs no cost."""
        if self._deploy_planner is not None:
            return self._deploy_planner()
        return _default_deploy_what_if(self._deployments())

    def deploy_apply(
        self,
        expected_params_hash: Optional[str] = None,
        expected_plan_hash: Optional[str] = None,
        expected_template_hash: Optional[str] = None,
        no_wait: bool = True,
    ) -> Dict[str, Any]:
        """Execute create_or_update (the APPLY phase). BILLABLE; broker/two-phase gated.

        ``expected_plan_hash`` + ``expected_template_hash`` bind the apply to the
        EXACT change set AND compiled template that were approved at plan time: the
        default path re-checks the template and re-runs what-if immediately before
        committing, refusing on any drift (not just identical params). Both are
        REQUIRED — the default path fails loud if either is missing."""
        if self._deploy_applier is not None:
            return self._deploy_applier(
                expected_params_hash=expected_params_hash,
                expected_plan_hash=expected_plan_hash,
                expected_template_hash=expected_template_hash,
                no_wait=no_wait,
            )
        return _default_deploy_apply(
            self._deployments(), expected_params_hash, expected_plan_hash,
            expected_template_hash, no_wait
        )


# ---------------------------------------------------------------------------
# Default deploy plan/apply (reuse the canonical deploy.py helpers; lazy imports)
# ---------------------------------------------------------------------------


def _require_providers_registered() -> None:
    """Abort BEFORE any deploy step if a required resource provider is NotRegistered.

    Mirrors deploy.py's pre-flight: only a subscription admin can register
    providers, so failing loud here turns an opaque mid-deploy ARM error into a
    clear, actionable message.
    """
    try:
        from azure.mgmt.resource import ResourceManagementClient
    except ImportError as e:  # pragma: no cover - dependency declared
        raise AzureBackendError("azure-mgmt-resource is not installed.") from e
    from infra.mydude.local import azure_common as az
    from infra.mydude.local import deploy as _deploy

    try:
        rmc = ResourceManagementClient(az.credential(), az.subscription_id())
    except az.AzureWiringError as e:
        raise AzureBackendError(str(e)) from e
    bad = []
    for ns in _deploy.NEEDED_PROVIDERS:
        try:
            if rmc.providers.get(ns).registration_state == "NotRegistered":
                bad.append(ns)
        except Exception:  # noqa: BLE001
            pass  # a transient read error is not a hard block; ARM validate backstops
    if bad:
        raise AzureBackendError(
            "Resource provider(s) NotRegistered (a subscription admin must register "
            "them before deploy): %s" % ", ".join(bad)
        )


def _compile_template_and_params():
    """Compile bicep -> ARM JSON and load deploy parameters (fail-loud)."""
    from infra.mydude.local import deploy as _deploy

    try:
        template = _deploy.compile_template()
        params = _deploy.load_parameters()
    except SystemExit as e:  # load_parameters() sys.exit(2) on unfilled params
        raise AzureBackendError(
            "Deploy parameters are unfilled/invalid; refusing to proceed."
        ) from e
    except KeyError as e:  # required secret env var missing
        raise AzureBackendError("Missing required deploy secret env var: %s" % e) from e
    except Exception as e:  # noqa: BLE001
        raise AzureBackendError("Failed to compile Bicep template: %s" % (str(e)[:300])) from e
    return template, params


def _flatten_delta(delta) -> List[Dict[str, Any]]:
    """Flatten an ARM what-if property-delta tree into a sorted (path, type) list.

    Only the property PATH and the change kind are fingerprinted — never the
    before/after VALUES (which can contain secrets). Recursing into ``children``
    and sorting makes the result deterministic and order-independent, so the plan
    hash captures property-level drift (e.g. a changed SKU on the same resource)
    without ever exposing or depending on the values themselves.
    """
    out: List[Dict[str, Any]] = []
    for pc in (delta or []):
        out.append({
            "path": getattr(pc, "path", None),
            "type": str(getattr(pc, "property_change_type", None)),
        })
        children = getattr(pc, "children", None)
        if children:
            out.extend(_flatten_delta(children))
    return sorted(out, key=lambda d: (str(d.get("path")), str(d.get("type"))))


def _extract_changes(res) -> List[Dict[str, Any]]:
    """Normalize an ARM what-if result into the canonical change list.

    Shared by the plan phase AND the pre-apply drift re-check so both compute the
    IDENTICAL structure that :func:`compute_plan_hash` fingerprints — any skew here
    would let the drift guard pass/fail spuriously. The ``delta`` captures
    property-level changes (paths + kinds, values excluded) so a same-resource
    property change cannot reuse a stale plan hash.
    """
    return [
        {"change_type": str(getattr(ch, "change_type", None)),
         "resource_id": getattr(ch, "resource_id", None),
         "delta": _flatten_delta(getattr(ch, "delta", None))}
        for ch in (getattr(res, "changes", None) or [])
    ]


def _default_deploy_what_if(dmc) -> Dict[str, Any]:
    from infra.mydude.local import azure_common as az

    _require_providers_registered()
    template, params = _compile_template_and_params()
    payload = {"properties": {"mode": "Incremental", "template": template, "parameters": params}}
    try:
        res = dmc.deployments.begin_what_if(az.RG_NAME, az.DEPLOYMENT_NAME, payload).result()
    except Exception as e:  # noqa: BLE001
        raise AzureBackendError("what-if failed: %s" % (str(e)[:300])) from e
    changes = _extract_changes(res)
    # NB: params (which contain secrets) are NEVER returned — only their fingerprint.
    return {
        "changes": changes,
        "change_count": len(changes),
        "template_resource_count": len(template.get("resources", [])),
        "params_hash": compute_params_hash(params),
        "template_hash": compute_template_hash(template),
    }


def _default_deploy_apply(
    dmc,
    expected_params_hash: Optional[str],
    expected_plan_hash: Optional[str],
    expected_template_hash: Optional[str],
    no_wait: bool,
) -> Dict[str, Any]:
    from infra.mydude.local import azure_common as az

    # Fail-loud (pillar #1/#4): a billable, irreversible apply must NEVER run
    # without the full approved-plan binding — even on a direct backend call that
    # bypasses the broker. ALL THREE of params/plan/template hash are required, so
    # the apply can only submit the EXACT reviewed payload. Params are mandatory
    # too: they carry deployment effects AND secrets not fully represented by the
    # compiled template or the sanitized (value-free) what-if delta.
    if not expected_params_hash or not expected_plan_hash or not expected_template_hash:
        raise AzureBackendError(
            "Refusing to apply without a complete approved-plan binding "
            "(params_hash + plan_hash + template_hash); re-plan required."
        )
    _require_providers_registered()
    template, params = _compile_template_and_params()
    actual = compute_params_hash(params)
    if actual != expected_params_hash:
        raise AzureBackendError(
            "Deploy parameters changed since the plan was approved; re-plan required."
        )
    # Template drift: a changed template can produce the SAME {change_type,
    # resource_id} set while applying different property values, so bind the exact
    # compiled template too — (template_hash + params_hash) pin the byte-for-byte
    # payload, closing the same-resource property/effect drift gap.
    actual_template = compute_template_hash(template)
    if actual_template != expected_template_hash:
        raise AzureBackendError(
            "The compiled deployment template changed since approval (drift "
            "detected); re-plan required before applying."
        )
    payload = {"properties": {"mode": "Incremental", "template": template, "parameters": params}}
    # Drift guard (pillar #4): re-run what-if against the LIVE stack immediately
    # before committing and bind the apply to the EXACT change set that was reviewed.
    # Identical params/template alone are NOT enough — live resource state can drift
    # after plan time, so a stale token must never authorize a surprise change.
    try:
        wi = dmc.deployments.begin_what_if(az.RG_NAME, az.DEPLOYMENT_NAME, payload).result()
    except Exception as e:  # noqa: BLE001
        raise AzureBackendError("pre-apply what-if failed: %s" % (str(e)[:300])) from e
    actual_plan = compute_plan_hash(_extract_changes(wi))
    if actual_plan != expected_plan_hash:
        raise AzureBackendError(
            "The live deployment plan changed since approval (drift detected); "
            "re-plan required before applying."
        )
    try:
        poller = dmc.deployments.begin_create_or_update(az.RG_NAME, az.DEPLOYMENT_NAME, payload)
    except Exception as e:  # noqa: BLE001
        raise AzureBackendError("deploy create_or_update failed to submit: %s" % (str(e)[:300])) from e
    if no_wait:
        # NB: never echo params_hash/template_hash back — they are fingerprints of
        # secret-bearing parameters and stay bound INSIDE the token only.
        return {"submitted": True, "no_wait": True, "state": "Running",
                "deployment": az.DEPLOYMENT_NAME}
    try:
        res = poller.result()
    except Exception as e:  # noqa: BLE001
        raise AzureBackendError("deploy failed: %s" % (str(e)[:300])) from e
    outputs = {
        k: (spec.get("value") if isinstance(spec, dict) else spec)
        for k, spec in (getattr(res.properties, "outputs", None) or {}).items()
    }
    # NB: never echo params_hash/template_hash back — they are fingerprints of
    # secret-bearing parameters and stay bound INSIDE the token only.
    return _jsonable({
        "submitted": True,
        "no_wait": False,
        "state": getattr(res.properties, "provisioning_state", None),
        "deployment": az.DEPLOYMENT_NAME,
        "outputs": outputs,
    })
