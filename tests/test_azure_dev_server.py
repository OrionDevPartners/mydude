"""Tests for the Azure MCP dev-accelerator HTTP server (Task #186, T4).

These run completely offline — no Azure, no network beyond the in-process
Starlette test client, no live database. The governed broker is replaced with a
recording fake so these tests exercise the SERVER layer (tool schemas, param
building, result mapping, fail-loud auth, transport-security wiring); the actual
contract→policy→broker→handler→audit enforcement is covered by
``tests/test_azure_governance.py``.

Coverage:
  * Tool surface: the six ``azure_*`` tools are registered with input schemas and
    correct read-only/destructive annotations.
  * Auth: ``load_expected_token`` prefers the env var, falls back to Key Vault,
    and FAILS LOUD when neither yields a token; constant-time token compare.
  * Auth middleware: health is open; any other path needs a valid bearer token
    (401 otherwise) — the guarantee that the HTTP MCP server is never open.
  * Transport security: env allow-list keeps DNS-rebinding protection on; the
    explicit opt-out turns it off; default defers to the SDK.
  * Dispatch mapping: structured output returned on success; governance blocks
    and honest handler failures both surface as actionable ValueErrors; optional
    params are omitted unless provided; the destructive apply forwards its
    token/hash/confirm fields and source tag.

Runnable two ways:
  * ``python tests/test_azure_dev_server.py``   (standalone; exits non-zero on failure)
  * ``pytest tests/test_azure_dev_server.py``    (test_* functions; no plugins needed)
"""
import asyncio
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from starlette.testclient import TestClient

import src.mcp.azure_dev_server as S

EXPECTED_TOOLS = {
    "azure_cosmos_read", "azure_pg_select", "azure_deploy_status",
    "azure_aoai_complete", "azure_deploy_plan", "azure_deploy_apply",
}


# -- helpers ------------------------------------------------------------------

class _FakeBroker:
    """Records (capability, params) and returns a canned BrokerResult-like."""

    def __init__(self, result):
        self._result = result
        self.calls = []

    async def request(self, capability, params):
        self.calls.append((capability, dict(params)))
        return self._result


def _ok(payload):
    return types.SimpleNamespace(
        ok=True,
        decision=types.SimpleNamespace(allowed=True, reason=""),
        output=json.dumps(payload),
    )


def _blocked(reason):
    return types.SimpleNamespace(
        ok=False,
        decision=types.SimpleNamespace(allowed=False, reason=reason),
        output=None,
    )


class _install_broker:
    """Context manager: swap the module broker singleton for a fake, then restore."""

    def __init__(self, result):
        self.fake = _FakeBroker(result)
        self._prev = None

    def __enter__(self):
        self._prev = S._BROKER
        S._BROKER = self.fake
        return self.fake

    def __exit__(self, *exc):
        S._BROKER = self._prev
        return False


class _env:
    """Context manager: set/clear env vars and restore them afterwards."""

    def __init__(self, **kv):
        self._kv = kv
        self._saved = {}

    def __enter__(self):
        for k, v in self._kv.items():
            self._saved[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *exc):
        for k, old in self._saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old
        return False


def _call(coro):
    return asyncio.run(coro)


# -- tool surface -------------------------------------------------------------

def test_list_tools_exposes_six_azure_tools_with_schemas():
    tools = _call(S.mcp.list_tools())
    names = {t.name for t in tools}
    assert EXPECTED_TOOLS <= names, f"missing tools: {EXPECTED_TOOLS - names}"
    by = {t.name: t for t in tools}
    for name in EXPECTED_TOOLS:
        assert name.startswith("azure_"), name
        schema = by[name].inputSchema
        assert isinstance(schema, dict) and schema.get("type") == "object", name


def test_tool_annotations_mark_reads_and_destructive_apply():
    by = {t.name: t for t in _call(S.mcp.list_tools())}
    for ro in ("azure_cosmos_read", "azure_pg_select", "azure_deploy_status",
               "azure_deploy_plan"):
        assert by[ro].annotations.readOnlyHint is True, ro
        assert by[ro].annotations.destructiveHint is False, ro
    apply = by["azure_deploy_apply"].annotations
    assert apply.readOnlyHint is False
    assert apply.destructiveHint is True


# -- auth: token sourcing -----------------------------------------------------

def test_load_token_prefers_env_and_skips_keyvault():
    called = {"kv": False}

    def _getter(name):
        called["kv"] = True
        return "kv-token"

    with _env(AZURE_MCP_AUTH_TOKEN="env-token"):
        tok = S.load_expected_token(kv_getter=_getter)
    assert tok == "env-token"
    assert called["kv"] is False


def test_load_token_falls_back_to_keyvault():
    seen = {}

    def _getter(name):
        seen["name"] = name
        return "  kv-token  "  # whitespace is stripped

    with _env(AZURE_MCP_AUTH_TOKEN=None, AZURE_MCP_AUTH_SECRET_NAME="custom-secret"):
        tok = S.load_expected_token(kv_getter=_getter)
    assert tok == "kv-token"
    assert seen["name"] == "custom-secret"


def test_load_token_fails_loud_when_absent():
    with _env(AZURE_MCP_AUTH_TOKEN=None, AZURE_MCP_AUTH_SECRET_NAME=None):
        try:
            S.load_expected_token(kv_getter=lambda name: None)
        except S.AzureMcpAuthError:
            pass
        else:
            raise AssertionError("expected AzureMcpAuthError when no token is available")


def test_load_token_fails_loud_when_keyvault_errors():
    def _boom(name):
        raise RuntimeError("kv unreachable")

    with _env(AZURE_MCP_AUTH_TOKEN=None):
        try:
            S.load_expected_token(kv_getter=_boom)
        except S.AzureMcpAuthError:
            pass
        else:
            raise AssertionError("expected AzureMcpAuthError when Key Vault read fails")


def test_token_matches_is_constant_time_correct():
    assert S._token_matches("abc", "abc") is True
    assert S._token_matches("abc", "abd") is False
    assert S._token_matches("", "abc") is False
    assert S._token_matches("abc", "") is False


# -- auth middleware ----------------------------------------------------------

def test_health_is_open_and_mcp_requires_token():
    app = S.build_asgi_app("secret-xyz", transport_security=None)
    with TestClient(app) as c:
        assert c.get(S.HEALTH_PATH).status_code == 200
        r = c.post(S.MCP_PATH, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert r.status_code == 401
        assert "bearer" in (r.headers.get("www-authenticate", "").lower())
        r = c.post(S.MCP_PATH, headers={"Authorization": "Bearer nope"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert r.status_code == 401


def test_build_app_refuses_without_token():
    try:
        S.build_asgi_app("", transport_security=None)
    except S.AzureMcpAuthError:
        pass
    else:
        raise AssertionError("build_asgi_app must refuse an empty token")


# -- transport security -------------------------------------------------------

def test_transport_security_allowlist_keeps_protection_on():
    with _env(AZURE_MCP_ALLOWED_HOSTS="app.internal,foo", AZURE_MCP_ALLOWED_ORIGINS=None,
              AZURE_MCP_DISABLE_HOST_CHECK=None):
        ts = S.transport_security_from_env()
    assert ts is not None
    assert ts.enable_dns_rebinding_protection is True
    assert "app.internal" in ts.allowed_hosts


def test_transport_security_explicit_optout_disables():
    with _env(AZURE_MCP_ALLOWED_HOSTS=None, AZURE_MCP_ALLOWED_ORIGINS=None,
              AZURE_MCP_DISABLE_HOST_CHECK="true"):
        ts = S.transport_security_from_env()
    assert ts is not None
    assert ts.enable_dns_rebinding_protection is False


def test_transport_security_default_is_none():
    with _env(AZURE_MCP_ALLOWED_HOSTS=None, AZURE_MCP_ALLOWED_ORIGINS=None,
              AZURE_MCP_DISABLE_HOST_CHECK=None):
        assert S.transport_security_from_env() is None


# -- dispatch mapping ---------------------------------------------------------

def test_cosmos_tool_returns_structured_output_and_tags_source():
    with _install_broker(_ok({"ok": True, "items": [1, 2], "count": 2})) as fake:
        out = _call(S.azure_cosmos_read_tool(
            database="db", container="c", query="SELECT * FROM c"))
    assert out["items"] == [1, 2]
    cap, params = fake.calls[0]
    assert cap == "azure_cosmos_read"
    assert params["source"] == "mcp:azure_cosmos_read"
    assert params["database"] == "db" and params["query"].startswith("SELECT")
    # optional params omitted when not provided
    assert "parameters" not in params and "max_items" not in params


def test_cosmos_tool_forwards_optional_params():
    with _install_broker(_ok({"ok": True, "items": []})) as fake:
        _call(S.azure_cosmos_read_tool(
            database="db", container="c", query="SELECT 1",
            parameters=[{"name": "@x", "value": 1}], max_items=5))
    _, params = fake.calls[0]
    assert params["parameters"] == [{"name": "@x", "value": 1}]
    assert params["max_items"] == 5


def test_pg_select_omits_optional_then_forwards_them():
    with _install_broker(_ok({"ok": True, "rows": []})) as fake:
        _call(S.azure_pg_select_tool(db_key="agents_home", sql="SELECT 1"))
    _, p1 = fake.calls[0]
    assert "params" not in p1 and "max_rows" not in p1
    with _install_broker(_ok({"ok": True, "rows": []})) as fake:
        _call(S.azure_pg_select_tool(
            db_key="agents_home", sql="SELECT 1", params=[1], max_rows=10))
    _, p2 = fake.calls[0]
    assert p2["params"] == [1] and p2["max_rows"] == 10


def test_governance_block_raises_value_error_with_reason():
    with _install_broker(_blocked("ENABLE_AZURE_MCP is false")):
        try:
            _call(S.azure_deploy_status_tool())
        except ValueError as e:
            assert "ENABLE_AZURE_MCP" in str(e)
        else:
            raise AssertionError("a governance block must raise ValueError")


def test_handler_honest_failure_raises_sanitized_error():
    with _install_broker(_ok({"ok": False, "error": "Azure backend is not configured"})):
        try:
            _call(S.azure_deploy_status_tool())
        except ValueError as e:
            assert "not configured" in str(e)
        else:
            raise AssertionError("an honest handler failure must raise ValueError")


def test_empty_output_raises_runtime_error():
    res = types.SimpleNamespace(
        ok=True, decision=types.SimpleNamespace(allowed=True, reason=""), output=None)
    with _install_broker(res):
        try:
            _call(S.azure_deploy_status_tool())
        except RuntimeError:
            pass
        else:
            raise AssertionError("missing output must fail loud")


def test_deploy_apply_forwards_token_hash_confirm():
    with _install_broker(_ok({"ok": True, "status": "started"})) as fake:
        out = _call(S.azure_deploy_apply_tool(
            plan_token="tok", plan_hash="h123", confirm="APPLY AZURE DEPLOYMENT"))
    assert out["status"] == "started"
    cap, params = fake.calls[0]
    assert cap == "azure_deploy_apply"
    assert params["plan_token"] == "tok" and params["plan_hash"] == "h123"
    assert params["confirm"] == "APPLY AZURE DEPLOYMENT"
    assert params["source"] == "mcp:azure_deploy_apply"


def test_aoai_and_plan_tools_tag_governed_source():
    with _install_broker(_ok({"ok": True, "synthesis": "x"})) as fake:
        _call(S.azure_aoai_complete_tool(prompt="hi"))
    cap, params = fake.calls[0]
    assert cap == "azure_aoai_complete"
    assert params["domain"] == "general" and params["team"] == "default"
    assert params["source"] == "mcp:azure_aoai_complete"
    with _install_broker(_ok({"ok": True, "plan_hash": "h", "plan_token": "t"})) as fake:
        _call(S.azure_deploy_plan_tool())
    cap, params = fake.calls[0]
    assert cap == "azure_deploy_plan"
    assert params["actor"] == "mcp-client"
    assert params["source"] == "mcp:azure_deploy_plan"


def _run_all():
    tests = [
        test_list_tools_exposes_six_azure_tools_with_schemas,
        test_tool_annotations_mark_reads_and_destructive_apply,
        test_load_token_prefers_env_and_skips_keyvault,
        test_load_token_falls_back_to_keyvault,
        test_load_token_fails_loud_when_absent,
        test_load_token_fails_loud_when_keyvault_errors,
        test_token_matches_is_constant_time_correct,
        test_health_is_open_and_mcp_requires_token,
        test_build_app_refuses_without_token,
        test_transport_security_allowlist_keeps_protection_on,
        test_transport_security_explicit_optout_disables,
        test_transport_security_default_is_none,
        test_cosmos_tool_returns_structured_output_and_tags_source,
        test_cosmos_tool_forwards_optional_params,
        test_pg_select_omits_optional_then_forwards_them,
        test_governance_block_raises_value_error_with_reason,
        test_handler_honest_failure_raises_sanitized_error,
        test_empty_output_raises_runtime_error,
        test_deploy_apply_forwards_token_hash_confirm,
        test_aoai_and_plan_tools_tag_governed_source,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print("PASS", t.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL", t.__name__, "->", e)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("ERROR", t.__name__, "->", type(e).__name__, e)
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
