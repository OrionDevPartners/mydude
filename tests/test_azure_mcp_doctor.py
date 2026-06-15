"""Tests for the Azure MCP dev-accelerator doctor's address-pinning advisory.

Task #220 made the governed MCP server pin itself to its own address after the
first deploy (DNS-rebinding Host-header hardening). The server-side half of that
— ``transport_security_from_env`` in ``src/mcp/azure_dev_server.py`` — already
has coverage in ``tests/test_azure_dev_server.py`` (allow-list ON, opt-out OFF,
default None). This file covers the *operator-facing* half: the doctor's
container-app advisory in ``infra/mydude/local/azure_mcp_doctor.py``
(``_check_container_app``), which WARNs while the host check is unpinned and
reports "pinned" once the app FQDN is set.

These run completely offline: the only Azure surface is ``rmc.resources
.get_by_id``, which is replaced with a fake that returns a canned container-app
resource. No network, no credentials, no live ARM. We only exercise the pure
env-dict parsing in the advisory, so no real Azure call is needed.

Runnable two ways:
  * ``python tests/test_azure_mcp_doctor.py``   (standalone; exits non-zero on failure)
  * ``pytest tests/test_azure_mcp_doctor.py``    (test_* functions; no plugins needed)
"""
import os
import sys
import types

# The doctor lives in infra/mydude/local and does ``import azure_common as az``,
# so that directory must be importable for the module to load.
_DOCTOR_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "infra", "mydude", "local",
)
sys.path.insert(0, _DOCTOR_DIR)

import azure_mcp_doctor as D  # noqa: E402

#: Env that satisfies every HARD container-app check, so each test isolates the
#: advisory (host-check pinning) as the only variable.
BASE_ENV = {
    "ENABLE_AZURE_MCP": "true",
    "AZURE_MCP_AUTH_SECRET_NAME": "azure-mcp-auth-token",
}


# -- helpers ------------------------------------------------------------------

def _resource(env, *, external=False, identity_type="UserAssigned"):
    """Build a fake Container App resource the doctor knows how to read."""
    props = {
        "configuration": {"ingress": {"external": external}},
        "template": {
            "containers": [
                {"env": [{"name": k, "value": v} for k, v in env.items()]}
            ]
        },
    }
    return types.SimpleNamespace(properties=props, identity={"type": identity_type})


class _FakeResources:
    def __init__(self, res):
        self._res = res

    def get_by_id(self, resource_id, api_version):
        return self._res


class _FakeRmc:
    def __init__(self, res):
        self.resources = _FakeResources(res)


def _check(env, **kw):
    """Run the advisory against a canned resource and return (ok, detail)."""
    return D._check_container_app(_FakeRmc(_resource(env, **kw)))


# -- advisory: unpinned host check WARNs --------------------------------------

def test_advisory_warns_when_allowed_hosts_empty():
    ok, detail = _check(BASE_ENV)
    # The advisory is non-fatal — the hard checks all pass.
    assert ok is True
    assert "WARN host check not pinned" in detail
    assert "AZURE_MCP_ALLOWED_HOSTS" in detail


def test_advisory_warns_when_host_check_explicitly_disabled():
    # Even WITH an allow-list, an explicit opt-out must still warn (the opt-out
    # is what re-opens the host check, so it should never be left enabled).
    env = dict(BASE_ENV,
               AZURE_MCP_ALLOWED_HOSTS="app.internal.azurecontainerapps.io",
               AZURE_MCP_DISABLE_HOST_CHECK="true")
    ok, detail = _check(env)
    assert ok is True
    assert "WARN host check not pinned" in detail
    assert "host check pinned" not in detail


def test_advisory_warns_for_each_truthy_optout_form():
    for val in ("1", "true", "yes", "on", "TRUE", "On"):
        env = dict(BASE_ENV, AZURE_MCP_DISABLE_HOST_CHECK=val)
        ok, detail = _check(env)
        assert ok is True, val
        assert "WARN host check not pinned" in detail, val


# -- advisory: pinned host check reports clean --------------------------------

def test_advisory_reports_pinned_when_hosts_set_and_not_disabled():
    env = dict(BASE_ENV,
               AZURE_MCP_ALLOWED_HOSTS="mydude-azure-mcp.internal.azurecontainerapps.io")
    ok, detail = _check(env)
    assert ok is True
    assert "host check pinned" in detail
    assert "WARN host check not pinned" not in detail


def test_advisory_pinned_ignores_falsey_disable_value():
    # A falsey/absent opt-out next to a real allow-list still counts as pinned.
    env = dict(BASE_ENV,
               AZURE_MCP_ALLOWED_HOSTS="app.internal",
               AZURE_MCP_DISABLE_HOST_CHECK="false")
    ok, detail = _check(env)
    assert ok is True
    assert "host check pinned" in detail
    assert "WARN host check not pinned" not in detail


# -- advisory is independent of the hard PASS/FAIL verdict --------------------

def test_advisory_does_not_change_hard_verdict():
    # Pinned vs unpinned must never flip the overall ok — it's defense-in-depth,
    # not a gate. Both states keep the (passing) hard verdict True.
    ok_unpinned, _ = _check(BASE_ENV)
    ok_pinned, _ = _check(dict(BASE_ENV, AZURE_MCP_ALLOWED_HOSTS="app.internal"))
    assert ok_unpinned is True and ok_pinned is True

    # And a hard failure (external ingress) stays FAIL regardless of pinning.
    ok_bad, detail_bad = _check(
        dict(BASE_ENV, AZURE_MCP_ALLOWED_HOSTS="app.internal"), external=True)
    assert ok_bad is False
    assert "host check pinned" in detail_bad  # advisory still reported
    assert "ingress is EXTERNAL" in detail_bad


def _run_all():
    tests = [
        test_advisory_warns_when_allowed_hosts_empty,
        test_advisory_warns_when_host_check_explicitly_disabled,
        test_advisory_warns_for_each_truthy_optout_form,
        test_advisory_reports_pinned_when_hosts_set_and_not_disabled,
        test_advisory_pinned_ignores_falsey_disable_value,
        test_advisory_does_not_change_hard_verdict,
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
