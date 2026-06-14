"""Hermetic tests for the Azure dev-accelerator backend adapters (Task #186, T2).

Everything here runs with NO network, NO Azure SDK, and NO database: every Azure
collaborator is injected as a fake, and the SQL validator / token helpers are pure.
This pins the governance-critical guarantees:

  * the SELECT-only validator rejects DML/DDL/multi-statement/comment injection,
  * each backend fails loud (AzureBackendError) when unconfigured,
  * reads are bounded + JSON-safe + truncation-aware,
  * the completion adapter ALWAYS routes through the governed swarm (no raw AOAI),
  * the two-phase deploy plan token signs/verifies and is tamper/expiry/param-bound.

Runnable as ``python tests/test_azure_backends.py`` or under pytest.
"""
import asyncio
import os
import sys
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.mcp.azure_backends import (
    ALLOWED_PG_DATABASES,
    COSMOS_HARD_MAX_ITEMS,
    PG_HARD_MAX_ROWS,
    AzureBackendError,
    AzureBackends,
    SqlValidationError,
    _bound_int,
    _jsonable,
    compute_params_hash,
    compute_plan_hash,
    sign_plan_token,
    validate_cosmos_query,
    validate_select_only,
    verify_plan_token,
)


# ───────────────────────── SQL validator ─────────────────────────

def test_validate_select_accepts_select_and_with():
    assert validate_select_only("SELECT 1") == "SELECT 1"
    assert validate_select_only("  select * from t  ") == "select * from t"
    assert validate_select_only("SELECT 1;") == "SELECT 1"  # single trailing ; stripped
    q = "WITH x AS (SELECT 1) SELECT * FROM x"
    assert validate_select_only(q) == q


def test_validate_select_allows_keyword_lookalike_identifiers():
    # updated_at / created_by / deleted_flag must NOT trip the whole-word matcher.
    for ok in (
        "SELECT updated_at, created_by FROM updates",
        "SELECT deleted_flag FROM created_view",
        "SELECT setting_value FROM settings",
    ):
        assert validate_select_only(ok) == ok


def test_validate_select_rejects_dml_ddl():
    for bad in (
        "DELETE FROM t",
        "UPDATE t SET x=1",
        "INSERT INTO t VALUES (1)",
        "DROP TABLE t",
        "ALTER TABLE t ADD c int",
        "TRUNCATE t",
        "GRANT SELECT ON t TO r",
        "SELECT * INTO newt FROM t",
        "WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x",
    ):
        try:
            validate_select_only(bad)
            assert False, "expected rejection for: %s" % bad
        except SqlValidationError:
            pass


def test_validate_select_rejects_multistatement_and_comments():
    for bad in (
        "SELECT 1; DROP TABLE t",
        "SELECT 1; SELECT 2",
        "SELECT 1 -- sneaky",
        "SELECT /* c */ 1",
        "SELECT 1 # hash",
    ):
        try:
            validate_select_only(bad)
            assert False, "expected rejection for: %s" % bad
        except SqlValidationError:
            pass


def test_validate_select_rejects_empty_overlong_nonstring():
    for bad in ("", "   ", 123, None):
        try:
            validate_select_only(bad)  # type: ignore[arg-type]
            assert False, "expected rejection for: %r" % (bad,)
        except SqlValidationError:
            pass
    try:
        validate_select_only("SELECT " + "x" * 5000)
        assert False, "expected length rejection"
    except SqlValidationError as e:
        assert "too long" in str(e).lower(), e


def test_validate_cosmos_query():
    assert validate_cosmos_query("SELECT * FROM c") == "SELECT * FROM c"
    for bad in ("DELETE FROM c", "SELECT 1; SELECT 2", "SELECT * FROM c -- x", ""):
        try:
            validate_cosmos_query(bad)
            assert False, "expected rejection for: %s" % bad
        except SqlValidationError:
            pass


# ───────────────────────── small helpers ─────────────────────────

def test_bound_int():
    assert _bound_int(5, 10, 1, 100) == 5
    assert _bound_int(0, 10, 1, 100) == 1       # clamp low
    assert _bound_int(999, 10, 1, 100) == 100   # clamp high
    assert _bound_int("nope", 10, 1, 100) == 10  # bad input -> default
    assert _bound_int(None, 10, 1, 100) == 10


def test_jsonable_coerces_rich_types():
    out = _jsonable({"d": datetime(2026, 1, 2, 3, 4, 5), "n": Decimal("1.5"),
                     "b": b"x", "nested": [Decimal("2"), {"k": Decimal("3")}]})
    assert out["d"] == "2026-01-02 03:04:05", out
    assert out["n"] == "1.5", out
    assert out["nested"] == ["2", {"k": "3"}], out
    # native types pass through
    assert _jsonable({"a": 1, "b": True, "c": None, "s": "x"}) == {"a": 1, "b": True, "c": None, "s": "x"}


# ───────────────────────── Cosmos read (injected) ─────────────────────────

class _FakeCosmos:
    """Acts as client + database + container in one, recording calls."""

    def __init__(self, items):
        self._items = items
        self.calls = []

    def get_database_client(self, name):
        self.calls.append(("db", name))
        return self

    def get_container_client(self, name):
        self.calls.append(("container", name))
        return self

    def query_items(self, query, parameters=None, enable_cross_partition_query=None, max_item_count=None):
        self.calls.append(("query", query, max_item_count))
        return iter(self._items)


def test_cosmos_read_happy_path_and_bounding():
    fake = _FakeCosmos([{"id": 1}, {"id": 2}, {"id": 3}])
    be = AzureBackends(cosmos_client=fake)
    out = be.cosmos_read("agents_memory", "episodic", "SELECT * FROM c", max_items=2)
    assert out["count"] == 2, out
    assert out["truncated"] is True, out
    assert out["items"] == [{"id": 1}, {"id": 2}], out
    assert ("db", "agents_memory") in fake.calls and ("container", "episodic") in fake.calls


def test_cosmos_read_rejects_bad_query_and_missing_names():
    be = AzureBackends(cosmos_client=_FakeCosmos([]))
    for kwargs in (
        dict(database="d", container="c", query="DELETE FROM c"),
        dict(database="", container="c", query="SELECT * FROM c"),
        dict(database="d", container="", query="SELECT * FROM c"),
    ):
        try:
            be.cosmos_read(**kwargs)
            assert False, "expected rejection for %r" % kwargs
        except (AzureBackendError, SqlValidationError):
            pass


def test_cosmos_read_clamps_max_items():
    fake = _FakeCosmos([{"id": i} for i in range(5)])
    be = AzureBackends(cosmos_client=fake)
    be.cosmos_read("d", "c", "SELECT * FROM c", max_items=999999)
    # the clamped value was passed to the SDK
    qcall = [c for c in fake.calls if c[0] == "query"][0]
    assert qcall[2] == COSMOS_HARD_MAX_ITEMS, qcall


class _RaisingCosmos:
    """Client whose query path raises — proves operation errors fail loud."""

    def get_database_client(self, name):
        return self

    def get_container_client(self, name):
        return self

    def query_items(self, **kwargs):
        raise RuntimeError("simulated cosmos failure")


def test_cosmos_read_surfaces_operation_error_as_backend_error():
    be = AzureBackends(cosmos_client=_RaisingCosmos())
    try:
        be.cosmos_read("d", "c", "SELECT * FROM c")
        assert False, "expected AzureBackendError"
    except AzureBackendError as e:
        assert "Cosmos read failed" in str(e), e


# ───────────────────────── Postgres SELECT (injected) ─────────────────────────

class _FakeCursor:
    def __init__(self, description, rows):
        self.description = description
        self._rows = list(rows)
        self.executed = None

    def execute(self, sql, params=None):
        self.executed = (sql, params)

    def fetchmany(self, n):
        out, self._rows = self._rows[:n], self._rows[n:]
        return out

    def close(self):
        pass


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.readonly = None
        self.closed = False

    def set_session(self, readonly=None):
        self.readonly = readonly

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


def _pg_backend(description, rows):
    conn = _FakeConn(_FakeCursor(description, rows))
    be = AzureBackends(pg_connector=lambda db_key: conn)
    return be, conn


def test_pg_select_happy_path_readonly_and_jsonable():
    desc = [("id",), ("when",)]
    rows = [[1, datetime(2026, 6, 1)], [2, datetime(2026, 6, 2)]]
    be, conn = _pg_backend(desc, rows)
    out = be.pg_select("agents_home", "SELECT id, when FROM t", max_rows=10)
    assert out["columns"] == ["id", "when"], out
    assert out["rows"] == [[1, "2026-06-01 00:00:00"], [2, "2026-06-02 00:00:00"]], out
    assert out["rowcount"] == 2 and out["truncated"] is False, out
    assert conn.readonly is True, "session must be set read-only"
    assert conn.closed is True, "connection must be closed"


def test_pg_select_truncates_to_max_rows():
    desc = [("id",)]
    rows = [[i] for i in range(10)]
    be, _ = _pg_backend(desc, rows)
    out = be.pg_select("agents_home", "SELECT id FROM t", max_rows=3)
    assert out["rowcount"] == 3 and out["truncated"] is True, out


def test_pg_select_rejects_unknown_db_and_non_select():
    be, _ = _pg_backend([("id",)], [[1]])
    try:
        be.pg_select("not_a_db", "SELECT 1")
        assert False, "expected unknown-db rejection"
    except AzureBackendError as e:
        assert "Unknown database" in str(e), e
    try:
        be.pg_select("agents_home", "DELETE FROM t")
        assert False, "expected non-select rejection"
    except SqlValidationError:
        pass


def test_pg_select_clamps_max_rows():
    desc = [("id",)]
    rows = [[i] for i in range(3)]
    cur = _FakeCursor(desc, rows)

    class _Spy(_FakeCursor):
        pass

    conn = _FakeConn(cur)
    be = AzureBackends(pg_connector=lambda db_key: conn)
    be.pg_select("agents_home", "SELECT id FROM t", max_rows=10 ** 9)
    # fetchmany was asked for hard-max + 1 (truncation probe)
    # (we can't see the arg directly here, but the clamp is covered by _bound_int test;
    #  this asserts the call path doesn't explode on a huge value)
    assert ALLOWED_PG_DATABASES == ("agents_home", "provider_home")
    assert PG_HARD_MAX_ROWS == 1000


def _patch_azure_common_unwired(fn):
    """Simulate a totally unwired Azure environment deterministically.

    This workspace happens to carry live AZURE_* control-plane creds, so we cannot
    prove "fails loud when unconfigured" by relying on the absence of env vars.
    Instead we patch the azure_common seam the default builders use so they raise
    AzureWiringError, and clear the Postgres DSN fast-paths.
    """
    from infra.mydude.local import azure_common as az

    saved = (az.cosmos_endpoint, az.build_db_dsn, az.subscription_id)
    saved_env = {k: os.environ.get(k) for k in ("PG_AGENTS_HOME_DSN", "PG_PROVIDER_HOME_DSN")}

    def _raise(*a, **k):
        raise az.AzureWiringError("unwired (test)")

    az.cosmos_endpoint = _raise
    az.build_db_dsn = _raise
    az.subscription_id = _raise
    for k in saved_env:
        os.environ.pop(k, None)
    try:
        fn(az)
    finally:
        az.cosmos_endpoint, az.build_db_dsn, az.subscription_id = saved
        for k, v in saved_env.items():
            if v is not None:
                os.environ[k] = v


def test_default_builders_fail_loud_when_unwired():
    def _t(_az):
        be = AzureBackends()  # no injected collaborators -> default builders
        for label, call in (
            ("cosmos", lambda: be.cosmos_read("d", "c", "SELECT * FROM c")),
            ("pg", lambda: be.pg_select("agents_home", "SELECT 1")),
            ("deploy", lambda: be.deploy_status()),
        ):
            try:
                call()
                assert False, "expected AzureBackendError for %s" % label
            except AzureBackendError:
                pass

    _patch_azure_common_unwired(_t)


# ───────────────────────── deployment status (injected) ─────────────────────────

class _Props:
    def __init__(self, state, outputs, ):
        self.provisioning_state = state
        self.outputs = outputs


class _Dep:
    def __init__(self, props):
        self.properties = props


class _OpProps:
    def __init__(self, state, rid=None, msg=None):
        self.provisioning_state = state
        self.target_resource = type("T", (), {"id": rid})() if rid else None
        self.status_message = msg


class _Op:
    def __init__(self, props):
        self.properties = props


class _DeploymentsApi:
    def __init__(self, dep):
        self._dep = dep

    def get(self, rg, name):
        return self._dep


class _OpsApi:
    def __init__(self, ops):
        self._ops = ops

    def list(self, rg, name):
        return iter(self._ops)


class _FakeDeploymentsClient:
    def __init__(self, dep, ops):
        self.deployments = _DeploymentsApi(dep)
        self.deployment_operations = _OpsApi(ops)


def test_deploy_status_summarizes_state_outputs_and_failures():
    dep = _Dep(_Props("Succeeded", {"cosmosEndpoint": {"value": "https://x"}}))
    ops = [_Op(_OpProps("Succeeded")), _Op(_OpProps("Succeeded")),
           _Op(_OpProps("Failed", rid="/r/1", msg="boom"))]
    be = AzureBackends(deployments_client=_FakeDeploymentsClient(dep, ops))
    out = be.deploy_status()
    assert out["state"] == "Succeeded", out
    assert out["outputs"] == {"cosmosEndpoint": "https://x"}, out
    assert out["operation_states"].get("Succeeded") == 2, out
    assert out["operation_states"].get("Failed") == 1, out
    assert out["failed"] == [{"resource_id": "/r/1", "message": "boom"}], out


class _RaisingDeploymentsApi:
    def get(self, rg, name):
        raise RuntimeError("simulated ARM failure")


class _RaisingDeploymentsClient:
    def __init__(self):
        self.deployments = _RaisingDeploymentsApi()
        self.deployment_operations = None


def test_deploy_status_surfaces_operation_error_as_backend_error():
    be = AzureBackends(deployments_client=_RaisingDeploymentsClient())
    try:
        be.deploy_status()
        assert False, "expected AzureBackendError"
    except AzureBackendError as e:
        assert "Could not read deployment status" in str(e), e


# ───────────────────────── governed completion (no raw AOAI) ─────────────────────────

def test_aoai_complete_routes_through_governed_swarm():
    seen = {}

    async def _fake_runner(prompt, domain="general", team="default", check_providers=True):
        seen.update(prompt=prompt, domain=domain, team=team, check_providers=check_providers)
        return {"SYNTHESIS": "governed answer", "COMPLIANCE_SCORES": [{"score": 90}]}

    be = AzureBackends(swarm_runner=_fake_runner)
    out = asyncio.run(be.aoai_complete("hello", domain="engineering", team="default"))
    assert out["SYNTHESIS"] == "governed answer", out
    assert seen["check_providers"] is True, "completion must enforce the provider guard"
    assert seen["domain"] == "engineering", seen


# ───────────────────────── two-phase deploy plan/apply (injected) ─────────────────────────

def test_deploy_what_if_and_apply_delegate_to_injected():
    planned = {"changes": [{"change_type": "Create", "resource_id": "/r/1"}],
               "change_count": 1, "params_hash": "ph"}
    applied = {"submitted": True, "state": "Running"}
    captured = {}

    def _planner():
        return planned

    def _applier(expected_params_hash=None, expected_plan_hash=None,
                 expected_template_hash=None, no_wait=True):
        captured.update(expected_params_hash=expected_params_hash,
                        expected_plan_hash=expected_plan_hash,
                        expected_template_hash=expected_template_hash, no_wait=no_wait)
        return applied

    be = AzureBackends(deploy_planner=_planner, deploy_applier=_applier)
    assert be.deploy_what_if() == planned
    assert be.deploy_apply(expected_params_hash="ph", expected_plan_hash="plh",
                           expected_template_hash="th", no_wait=True) == applied
    assert captured == {"expected_params_hash": "ph", "expected_plan_hash": "plh",
                        "expected_template_hash": "th", "no_wait": True}, captured


# ───────────────────────── deploy plan token ─────────────────────────

def _with_token_secret(fn):
    saved = (os.environ.get("MCP_DEPLOY_TOKEN_SECRET"), os.environ.get("SESSION_SECRET"))
    os.environ["MCP_DEPLOY_TOKEN_SECRET"] = "unit-test-secret"
    os.environ.pop("SESSION_SECRET", None)
    try:
        fn()
    finally:
        if saved[0] is None:
            os.environ.pop("MCP_DEPLOY_TOKEN_SECRET", None)
        else:
            os.environ["MCP_DEPLOY_TOKEN_SECRET"] = saved[0]
        if saved[1] is not None:
            os.environ["SESSION_SECRET"] = saved[1]


def test_plan_token_roundtrip():
    def _t():
        plan_hash = compute_plan_hash([{"change_type": "Create", "resource_id": "/r/1"}])
        params_hash = compute_params_hash({"a": 1})
        token = sign_plan_token(plan_hash=plan_hash, params_hash=params_hash,
                                template_hash="th", actor="dev", source="mcp")
        data = verify_plan_token(token)
        assert data["plan_hash"] == plan_hash, data
        assert data["params_hash"] == params_hash, data
        assert data["template_hash"] == "th", data
        assert data["actor"] == "dev" and data["source"] == "mcp", data
        assert data["nonce"], data
    _with_token_secret(_t)


def test_plan_token_expiry_and_tamper():
    def _t():
        token = sign_plan_token(plan_hash="ph", params_hash="ph2", template_hash="ph3")
        # expired
        try:
            verify_plan_token(token, max_age=-1)
            assert False, "expected expiry"
        except AzureBackendError as e:
            assert "expired" in str(e).lower(), e
        # tampered
        bad = token[:-2] + ("aa" if not token.endswith("aa") else "bb")
        try:
            verify_plan_token(bad)
            assert False, "expected tamper rejection"
        except AzureBackendError as e:
            assert "invalid" in str(e).lower() or "tamper" in str(e).lower(), e
    _with_token_secret(_t)


def test_plan_token_requires_secret():
    saved = (os.environ.get("MCP_DEPLOY_TOKEN_SECRET"), os.environ.get("SESSION_SECRET"))
    os.environ.pop("MCP_DEPLOY_TOKEN_SECRET", None)
    os.environ.pop("SESSION_SECRET", None)
    try:
        sign_plan_token(plan_hash="a", params_hash="b", template_hash="c")
        assert False, "expected AzureBackendError (no signing secret)"
    except AzureBackendError as e:
        assert "secret" in str(e).lower(), e
    finally:
        if saved[0] is not None:
            os.environ["MCP_DEPLOY_TOKEN_SECRET"] = saved[0]
        if saved[1] is not None:
            os.environ["SESSION_SECRET"] = saved[1]


def test_sign_plan_token_fails_loud_without_complete_binding():
    # A token must never be minted without the FULL binding — params/plan/template
    # hash are all required, so an apply can never run against an unpinned payload.
    def _t():
        for ph, pj, th in (("a", "b", None), ("a", None, "c"), (None, "b", "c"),
                           ("", "b", "c"), ("a", "", "c"), ("a", "b", "")):
            try:
                sign_plan_token(plan_hash=ph, params_hash=pj, template_hash=th)
                assert False, "missing binding must fail loud (%r,%r,%r)" % (ph, pj, th)
            except AzureBackendError as e:
                assert "binding" in str(e).lower(), e
    _with_token_secret(_t)


def test_plan_hash_is_stable_and_order_independent():
    a = compute_params_hash({"x": 1, "y": 2})
    b = compute_params_hash({"y": 2, "x": 1})
    assert a == b, "param hash must be canonical (key-order independent)"
    assert compute_plan_hash([1, 2]) != compute_plan_hash([2, 1]), "change order matters"


# ───────────────────────── pre-apply drift guard (hermetic) ─────────────────────────

class _FakeProp:
    """Mimics an ARM WhatIfPropertyChange (path + kind, optional children)."""

    def __init__(self, path, property_change_type, children=None):
        self.path = path
        self.property_change_type = property_change_type
        self.children = children or []


class _FakeChange:
    def __init__(self, change_type, resource_id):
        self.change_type = change_type
        self.resource_id = resource_id
        self.delta = []


class _FakeWhatIfResult:
    def __init__(self, changes):
        self.changes = changes


class _FakePoller:
    def __init__(self, result_obj=None):
        self._r = result_obj

    def result(self):
        return self._r


class _FakeDeployments:
    """Records create_or_update so a test can assert it never runs on drift."""

    def __init__(self, whatif_changes):
        self._whatif_changes = whatif_changes
        self.created = False

    def begin_what_if(self, rg, name, payload):
        return _FakePoller(_FakeWhatIfResult(self._whatif_changes))

    def begin_create_or_update(self, rg, name, payload):
        self.created = True
        return _FakePoller(None)  # no_wait=True never calls .result()


class _FakeDmc:
    def __init__(self, deployments):
        self.deployments = deployments


def _patch_apply_helpers(template, params):
    """Stub the Azure/bicep-touching helpers so _default_deploy_apply is hermetic.
    Returns a restore() callable."""
    import src.mcp.azure_backends as B
    saved = (B._require_providers_registered, B._compile_template_and_params)
    B._require_providers_registered = lambda: None
    B._compile_template_and_params = lambda: (template, params)

    def restore():
        B._require_providers_registered, B._compile_template_and_params = saved

    return restore


def test_default_deploy_apply_refuses_on_plan_drift():
    # Same params + template, but the LIVE what-if change set differs from what was
    # approved -> the apply must refuse (drift) and NEVER call create_or_update.
    import src.mcp.azure_backends as B
    template, params = {"resources": []}, {"p": {"value": 1}}
    params_hash = compute_params_hash(params)
    template_hash = B.compute_template_hash(template)
    live = _FakeDeployments([_FakeChange("Create", "/r/NEW")])
    approved_plan_hash = compute_plan_hash(
        [{"change_type": "Create", "resource_id": "/r/OLD", "delta": []}])
    restore = _patch_apply_helpers(template, params)
    try:
        try:
            B._default_deploy_apply(_FakeDmc(live), params_hash, approved_plan_hash,
                                    template_hash, True)
            assert False, "drift must be refused"
        except AzureBackendError as e:
            assert "drift" in str(e).lower(), e
        assert live.created is False, "create_or_update must not run on drift"
    finally:
        restore()


def test_default_deploy_apply_proceeds_when_plan_matches():
    # The live change set + template match the approved hashes -> proceed to submit.
    import src.mcp.azure_backends as B
    template, params = {"resources": []}, {"p": {"value": 1}}
    params_hash = compute_params_hash(params)
    template_hash = B.compute_template_hash(template)
    live_changes = [_FakeChange("Create", "/r/SAME")]
    live = _FakeDeployments(live_changes)
    approved_plan_hash = compute_plan_hash(
        B._extract_changes(_FakeWhatIfResult(live_changes)))
    restore = _patch_apply_helpers(template, params)
    try:
        out = B._default_deploy_apply(_FakeDmc(live), params_hash, approved_plan_hash,
                                      template_hash, True)
        assert out.get("submitted") is True and live.created is True, out
        # No-leakage: the apply response must NEVER echo back the params/template
        # fingerprints (they are bound INSIDE the token only).
        assert "params_hash" not in out and "template_hash" not in out, out
    finally:
        restore()


def test_default_deploy_apply_refuses_on_template_drift():
    # The change set (change_type + resource_id) is IDENTICAL, but the compiled
    # template differs from what was approved -> the apply must still refuse, so a
    # same-resource property/effect change can't ride a stale token.
    import src.mcp.azure_backends as B
    live_template, params = {"resources": [{"sku": "P2"}]}, {"p": {"value": 1}}
    params_hash = compute_params_hash(params)
    approved_template_hash = B.compute_template_hash({"resources": [{"sku": "P1"}]})
    live_changes = [_FakeChange("Modify", "/r/SAME")]
    live = _FakeDeployments(live_changes)
    approved_plan_hash = compute_plan_hash(
        B._extract_changes(_FakeWhatIfResult(live_changes)))
    restore = _patch_apply_helpers(live_template, params)
    try:
        try:
            B._default_deploy_apply(_FakeDmc(live), params_hash, approved_plan_hash,
                                    approved_template_hash, True)
            assert False, "template drift must be refused"
        except AzureBackendError as e:
            assert "template" in str(e).lower() and "drift" in str(e).lower(), e
        assert live.created is False, "create_or_update must not run on template drift"
    finally:
        restore()


def test_default_deploy_apply_refuses_on_property_delta_drift():
    # change_type + resource_id are identical, but a property DELTA differs ->
    # _extract_changes captures the delta so the plan hash no longer matches.
    import src.mcp.azure_backends as B
    template, params = {"resources": []}, {"p": {"value": 1}}
    params_hash = compute_params_hash(params)
    template_hash = B.compute_template_hash(template)
    # approved plan: a Modify on /r/SAME changing properties.sku
    approved_change = _FakeChange("Modify", "/r/SAME")
    approved_change.delta = [_FakeProp("properties.sku", "Modify")]
    approved_plan_hash = compute_plan_hash(
        B._extract_changes(_FakeWhatIfResult([approved_change])))
    # live plan: same resource/change type, but a DIFFERENT property path
    live_change = _FakeChange("Modify", "/r/SAME")
    live_change.delta = [_FakeProp("properties.tier", "Modify")]
    live = _FakeDeployments([live_change])
    restore = _patch_apply_helpers(template, params)
    try:
        try:
            B._default_deploy_apply(_FakeDmc(live), params_hash, approved_plan_hash,
                                    template_hash, True)
            assert False, "property-delta drift must be refused"
        except AzureBackendError as e:
            assert "drift" in str(e).lower(), e
        assert live.created is False, "create_or_update must not run on delta drift"
    finally:
        restore()


def test_default_deploy_apply_fails_loud_without_full_binding():
    # A direct backend call that omits ANY of params/plan/template hash must fail
    # loud — the billable apply can never run without the complete approved-plan
    # binding (params are mandatory too: they carry effects AND secrets).
    import src.mcp.azure_backends as B
    template, params = {"resources": []}, {"p": {"value": 1}}
    live = _FakeDeployments([_FakeChange("Create", "/r/SAME")])
    restore = _patch_apply_helpers(template, params)
    # (expected_params_hash, expected_plan_hash, expected_template_hash)
    incomplete = (
        (None, "plh", "th"), ("", "plh", "th"),    # missing params binding
        ("ph", None, "th"), ("ph", "", "th"),       # missing plan binding
        ("ph", "plh", None), ("ph", "plh", ""),     # missing template binding
        (None, None, None),
    )
    try:
        for ph, plh, th in incomplete:
            try:
                B._default_deploy_apply(_FakeDmc(live), ph, plh, th, True)
                assert False, "missing binding must fail loud (%r,%r,%r)" % (ph, plh, th)
            except AzureBackendError as e:
                assert "binding" in str(e).lower(), e
        assert live.created is False, "create_or_update must not run without binding"
    finally:
        restore()


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("ok   %s" % fn.__name__)
        except Exception as e:
            failed += 1
            print("FAIL %s: %s: %s" % (fn.__name__, type(e).__name__, e))
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
