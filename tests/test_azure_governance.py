"""Governance tests for the Azure MCP dev-accelerator capabilities (Task #186).

These run completely offline — no Azure, no network, no live database. Every
``AzureBackends`` collaborator fails loud with ``AzureBackendError`` when it is
not configured (no azure-sdk / no endpoints), so the suite asserts the honest
contract→policy→broker→handler→audit behaviour rather than faking a provider.

Coverage:
  * Contracts: the six ``azure_*`` capabilities are registered with the right
    epistemic category and required fields.
  * Contract preconditions (proof-of-governance BEFORE the policy gate):
      - SELECT-only Postgres rejects a non-allow-listed db + non-SELECT SQL.
      - Cosmos read rejects a non-SELECT query.
      - The billable APPLY phase rejects a missing/incorrect confirm phrase.
  * Policy:
      - reads/plan are allowed by default; ENABLE_AZURE_MCP=false hard-disables.
      - azure_deploy_apply is default-DENY and needs ALLOW_AZURE_DEPLOY=true.
  * Broker dispatch + audit:
      - a contract violation is rejected before policy (no output).
      - a policy-blocked apply is audited as "blocked" and never reaches Azure.
      - an allowed read reaches the handler and FAILS LOUD (no mock) honestly,
        and every path is audited.
  * Two-phase deploy token: sign/verify round-trip, tamper + expiry rejection,
    and the apply handler's plan-hash / token checks (all pre-Azure, hermetic).

Runnable two ways:
  * ``python tests/test_azure_governance.py``   (standalone; exits non-zero on failure)
  * ``pytest tests/test_azure_governance.py``    (test_* functions; no plugins needed)
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.swarm.broker as broker_mod
import src.swarm.integrations as integ_mod
from src.swarm.broker import CapabilityBroker
from src.swarm.capability_contracts import get_contract, validate_request
from src.swarm.integrations import Integrations
from src.swarm.policy import PolicyEngine
from src.mcp.azure_backends import (
    ALLOWED_PG_DATABASES,
    AZURE_DEPLOY_CONFIRM_PHRASE,
    AzureBackendError,
    compute_plan_hash,
    sign_plan_token,
    verify_plan_token,
)

AZURE_READ_CAPS = ("azure_cosmos_read", "azure_pg_select",
                   "azure_deploy_status", "azure_aoai_complete",
                   "azure_deploy_plan")
AZURE_ALL_CAPS = AZURE_READ_CAPS + ("azure_deploy_apply",)


# -- helpers ------------------------------------------------------------------

class _AuditRecorder:
    """Captures audit_capability(...) calls in-memory (no DB needed)."""

    def __init__(self):
        self.calls = []

    def __call__(self, capability, target=None, backend=None, status="ok",
                 detail=None, source=None):
        self.calls.append({
            "capability": capability, "target": target, "backend": backend,
            "status": status, "detail": detail, "source": source,
        })

    def for_cap(self, capability):
        return [c for c in self.calls if c["capability"] == capability]


class _patched_audit:
    """Rebind audit_capability in BOTH the broker + integrations namespaces."""

    def __enter__(self):
        self.rec = _AuditRecorder()
        self._b = broker_mod.audit_capability
        self._i = integ_mod.audit_capability
        broker_mod.audit_capability = self.rec
        integ_mod.audit_capability = self.rec
        return self.rec

    def __exit__(self, *exc):
        broker_mod.audit_capability = self._b
        integ_mod.audit_capability = self._i
        return False


class _env:
    """Temporarily set/unset environment variables, restoring on exit."""

    def __init__(self, **kv):
        self.kv = kv
        self._saved = {}

    def __enter__(self):
        for k, v in self.kv.items():
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


def _broker():
    return CapabilityBroker(PolicyEngine(), Integrations())


def _run(coro):
    return asyncio.run(coro)


# -- contracts registered -----------------------------------------------------

def test_all_azure_contracts_registered():
    expected_category = {
        "azure_cosmos_read": "knowledge",
        "azure_pg_select": "knowledge",
        "azure_deploy_status": "knowledge",
        "azure_aoai_complete": "mcp",
        "azure_deploy_plan": "skill",
        "azure_deploy_apply": "tool",
    }
    for cap in AZURE_ALL_CAPS:
        c = get_contract(cap)
        assert c is not None, "missing contract for %s" % cap
        assert str(c.category).lower().endswith(expected_category[cap]), \
            "%s category=%s" % (cap, c.category)
    # the destructive apply must require the durable two-phase fields.
    apply_c = get_contract("azure_deploy_apply")
    for f in ("plan_token", "plan_hash", "confirm"):
        assert f in apply_c.required_fields, "apply must require %s" % f


# -- contract preconditions: SELECT-only + allow-list -------------------------

def test_pg_select_rejects_non_allowlisted_db():
    v = validate_request("azure_pg_select",
                         {"db_key": "definitely_not_allowed", "sql": "SELECT 1"})
    assert v is not None and "Allowed" in v, v
    # a real allow-listed db with a valid SELECT passes the contract.
    ok = validate_request("azure_pg_select",
                          {"db_key": ALLOWED_PG_DATABASES[0], "sql": "SELECT 1"})
    assert ok is None, ok


def test_pg_select_rejects_non_select_sql():
    for bad in ("DELETE FROM users", "UPDATE t SET a=1",
                "SELECT 1; DROP TABLE t", "INSERT INTO t VALUES (1)"):
        v = validate_request("azure_pg_select",
                             {"db_key": ALLOWED_PG_DATABASES[0], "sql": bad})
        assert v is not None, "non-SELECT must be rejected: %r" % bad


def test_cosmos_read_rejects_non_select_query():
    v = validate_request("azure_cosmos_read", {
        "database": "db", "container": "c",
        "query": "SELECT * FROM c -- sneaky comment",
    })
    assert v is not None, v
    ok = validate_request("azure_cosmos_read", {
        "database": "db", "container": "c", "query": "SELECT * FROM c",
    })
    assert ok is None, ok


def test_deploy_apply_requires_exact_confirm_phrase():
    base = {"plan_token": "tok", "plan_hash": "abc"}
    # missing confirm -> required-field violation.
    assert validate_request("azure_deploy_apply", dict(base)) is not None
    # wrong confirm -> precondition violation.
    v = validate_request("azure_deploy_apply", {**base, "confirm": "yes do it"})
    assert v is not None and AZURE_DEPLOY_CONFIRM_PHRASE in v, v
    # exact confirm -> contract is satisfied.
    ok = validate_request("azure_deploy_apply",
                          {**base, "confirm": AZURE_DEPLOY_CONFIRM_PHRASE})
    assert ok is None, ok


# -- policy gate --------------------------------------------------------------

def test_policy_allows_reads_by_default():
    p = PolicyEngine()
    with _env(ENABLE_AZURE_MCP=None, ALLOW_AZURE_DEPLOY=None):
        for cap in AZURE_READ_CAPS:
            d = p.evaluate(cap, {})
            assert d.allowed is True, "%s should be allowed by default: %s" % (cap, d.reason)


def test_policy_master_disable_blocks_everything():
    p = PolicyEngine()
    with _env(ENABLE_AZURE_MCP="false"):
        for cap in AZURE_ALL_CAPS:
            d = p.evaluate(cap, {})
            assert d.allowed is False, "%s must be blocked when disabled" % cap
            assert "disabled" in (d.reason or "").lower(), d.reason


def test_policy_deploy_apply_is_default_deny():
    p = PolicyEngine()
    with _env(ENABLE_AZURE_MCP=None, ALLOW_AZURE_DEPLOY=None):
        d = p.evaluate("azure_deploy_apply", {})
        assert d.allowed is False, "apply must be default-deny"
        assert "ALLOW_AZURE_DEPLOY" in (d.reason or ""), d.reason
    with _env(ENABLE_AZURE_MCP=None, ALLOW_AZURE_DEPLOY="true"):
        d = p.evaluate("azure_deploy_apply", {})
        assert d.allowed is True, "apply allowed once explicitly opted in: %s" % d.reason


# -- broker: contract violation rejected before policy ------------------------

def test_broker_rejects_bad_sql_before_policy():
    with _patched_audit():
        res = _run(_broker().request(
            "azure_pg_select",
            {"db_key": ALLOWED_PG_DATABASES[0], "sql": "DROP TABLE t"}))
    assert res.decision.allowed is False, "non-SELECT must be rejected"
    assert res.output is None, "a rejected request must never reach the backend"


# -- broker: policy-blocked apply audited as blocked, never reaches Azure -----

def test_broker_blocks_default_deny_apply_and_audits():
    with _env(ENABLE_AZURE_MCP=None, ALLOW_AZURE_DEPLOY=None), _patched_audit() as rec:
        res = _run(_broker().request("azure_deploy_apply", {
            "plan_token": "tok", "plan_hash": "abc",
            "confirm": AZURE_DEPLOY_CONFIRM_PHRASE, "source": "test",
        }))
    assert res.decision.allowed is False, "apply must be blocked by default-deny policy"
    assert res.output is None
    blocked = [c for c in rec.for_cap("azure_deploy_apply") if c["status"] == "blocked"]
    assert blocked, "a policy-blocked apply must be audited as blocked"


# -- broker: allowed read reaches handler, fails loud, audited ----------------

def test_broker_allows_read_with_honest_output_and_audits():
    # A read capability is allowed by policy and reaches the handler. The
    # outcome must be HONEST and AUDITED either way: when Azure is reachable it
    # returns real ARM state (ok:true + a "state"); when it is not configured it
    # FAILS LOUD (ok:false + an "error"). Neither path may return a silent mock,
    # and every path writes an audit row.
    with _patched_audit() as rec:
        res = _run(_broker().request("azure_deploy_status", {"source": "test"}))
    assert res.decision.allowed is True, res.decision.reason
    assert res.output is not None
    out = json.loads(res.output)
    assert isinstance(out.get("ok"), bool), out
    if out["ok"]:
        assert out.get("state"), "a successful read must carry the real ARM state: %s" % out
    else:
        assert out.get("error"), "an unconfigured read must fail loud with an error: %s" % out
    assert rec.for_cap("azure_deploy_status"), "the read path must be audited"


def test_broker_governed_aoai_has_no_raw_passthrough_contract():
    # The governed-completion capability must be wired to the governed swarm,
    # never a raw model call. The contract documents this guarantee.
    c = get_contract("azure_aoai_complete")
    joined = " ".join(c.epistemic_preconditions).lower()
    assert "governed" in joined and "raw" in joined, c.epistemic_preconditions


# -- two-phase deploy token ---------------------------------------------------

def test_plan_token_roundtrip_and_tamper_rejected():
    plan_hash = compute_plan_hash([{"changeType": "Create", "resourceId": "/x"}])
    token = sign_plan_token(plan_hash=plan_hash, params_hash="ph",
                            template_hash="th", actor="dev", source="test")
    payload = verify_plan_token(token)
    assert payload.get("plan_hash") == plan_hash
    assert payload.get("params_hash") == "ph"
    assert payload.get("template_hash") == "th"
    # a tampered token must be rejected (fail loud).
    try:
        verify_plan_token(token + "x")
    except AzureBackendError:
        pass
    else:
        raise AssertionError("tampered plan token must be rejected")
    # an expired token must be rejected.
    try:
        verify_plan_token(token, max_age=-1)
    except AzureBackendError:
        pass
    else:
        raise AssertionError("expired plan token must be rejected")


def test_apply_handler_rejects_plan_hash_mismatch_pre_azure():
    # Opt in to apply at the policy layer; supply a VALID token but a plan_hash
    # that does not match what the token approved -> rejected before any Azure
    # call (hermetic), and audited as blocked.
    real_hash = compute_plan_hash([{"changeType": "Create", "resourceId": "/x"}])
    token = sign_plan_token(plan_hash=real_hash, params_hash="ph",
                            template_hash="th", actor="dev", source="test")
    with _env(ENABLE_AZURE_MCP=None, ALLOW_AZURE_DEPLOY="true"), _patched_audit() as rec:
        res = _run(_broker().request("azure_deploy_apply", {
            "plan_token": token, "plan_hash": "deadbeef",
            "confirm": AZURE_DEPLOY_CONFIRM_PHRASE, "source": "test",
        }))
    assert res.decision.allowed is True, "policy allows; mismatch caught in handler"
    out = json.loads(res.output)
    assert out.get("ok") is False and "match" in (out.get("error") or "").lower(), out
    blocked = [c for c in rec.for_cap("azure_deploy_apply") if c["status"] == "blocked"]
    assert blocked, "a plan_hash mismatch must be audited"


def test_apply_handler_rejects_tampered_token_pre_azure():
    real_hash = compute_plan_hash([{"changeType": "Create", "resourceId": "/x"}])
    token = sign_plan_token(plan_hash=real_hash, params_hash="ph",
                            template_hash="th", actor="dev", source="test")
    with _env(ENABLE_AZURE_MCP=None, ALLOW_AZURE_DEPLOY="true"), _patched_audit() as rec:
        res = _run(_broker().request("azure_deploy_apply", {
            "plan_token": token + "tamper", "plan_hash": real_hash,
            "confirm": AZURE_DEPLOY_CONFIRM_PHRASE, "source": "test",
        }))
    out = json.loads(res.output)
    assert out.get("ok") is False, out
    blocked = [c for c in rec.for_cap("azure_deploy_apply") if c["status"] == "blocked"]
    assert blocked, "a tampered token must be audited as blocked"


def test_apply_refuses_when_audit_cannot_be_guaranteed():
    # The billable, irreversible apply must REFUSE before touching Azure when a
    # DURABLE audit record cannot be written (pillar #4) — even with a valid
    # token + matching plan hash + the exact confirm phrase.
    import src.mcp.azure_backends as backends_mod
    real_hash = compute_plan_hash([{"change_type": "Create", "resource_id": "/x"}])
    token = sign_plan_token(plan_hash=real_hash, params_hash="ph",
                            template_hash="th", actor="dev", source="test")

    def _boom(*a, **k):
        raise integ_mod.AuditUnavailable("audit db down")

    class _NoApply:
        def deploy_apply(self, *a, **k):
            raise AssertionError("apply must NOT run when audit is unavailable")

    saved_strict = integ_mod.audit_capability_strict
    saved_be = backends_mod.AzureBackends
    integ_mod.audit_capability_strict = _boom
    backends_mod.AzureBackends = lambda *a, **k: _NoApply()
    try:
        with _env(ENABLE_AZURE_MCP=None, ALLOW_AZURE_DEPLOY="true"), \
                _patched_audit() as rec:
            res = _run(_broker().request("azure_deploy_apply", {
                "plan_token": token, "plan_hash": real_hash,
                "confirm": AZURE_DEPLOY_CONFIRM_PHRASE, "source": "test",
            }))
    finally:
        integ_mod.audit_capability_strict = saved_strict
        backends_mod.AzureBackends = saved_be
    out = json.loads(res.output)
    assert out.get("ok") is False, out
    assert "audit" in (out.get("error") or "").lower(), out
    # the refusal itself must be recorded best-effort as "blocked".
    blocked = [c for c in rec.for_cap("azure_deploy_apply") if c["status"] == "blocked"]
    assert blocked, "refusal must be audited (best-effort) as blocked"


def _run_all():
    tests = [
        test_all_azure_contracts_registered,
        test_pg_select_rejects_non_allowlisted_db,
        test_pg_select_rejects_non_select_sql,
        test_cosmos_read_rejects_non_select_query,
        test_deploy_apply_requires_exact_confirm_phrase,
        test_policy_allows_reads_by_default,
        test_policy_master_disable_blocks_everything,
        test_policy_deploy_apply_is_default_deny,
        test_broker_rejects_bad_sql_before_policy,
        test_broker_blocks_default_deny_apply_and_audits,
        test_broker_allows_read_with_honest_output_and_audits,
        test_broker_governed_aoai_has_no_raw_passthrough_contract,
        test_plan_token_roundtrip_and_tamper_rejected,
        test_apply_handler_rejects_plan_hash_mismatch_pre_azure,
        test_apply_handler_rejects_tampered_token_pre_azure,
        test_apply_refuses_when_audit_cannot_be_guaranteed,
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
