"""Tests for the Azure MCP dev-accelerator doctor's deployment health checks.

Task #220 made the governed MCP server pin itself to its own address after the
first deploy (DNS-rebinding Host-header hardening). The server-side half of that
— ``transport_security_from_env`` in ``src/mcp/azure_dev_server.py`` — already
has coverage in ``tests/test_azure_dev_server.py`` (allow-list ON, opt-out OFF,
default None). This file covers the *operator-facing* half: the doctor's checks
in ``infra/mydude/local/azure_mcp_doctor.py``:
  * ``_check_container_app`` — the HARD checks (internal ingress, user-assigned
    identity, ENABLE_AZURE_MCP=true, AZURE_MCP_AUTH_SECRET_NAME set, deploy-apply
    gate) plus the non-fatal host-pinning advisory.
  * ``_check_managed_env`` — the VNet-internal requirement.
  * ``_check_keyvault_token`` — the bearer-token secret presence check.

These run completely offline. The only Azure surfaces are ``rmc.resources
.get_by_id`` (replaced with a fake returning a canned resource) and, for the
Key Vault check, ``az.keyvault_uri`` / ``az.kv_get_secret`` (monkeypatched).
No network, no credentials, no live ARM — only the pure parsing logic is run.

Runnable two ways:
  * ``python tests/test_azure_mcp_doctor.py``   (standalone; exits non-zero on failure)
  * ``pytest tests/test_azure_mcp_doctor.py``    (test_* functions; no plugins needed)
"""
import os
import sys
import types
from contextlib import contextmanager

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

def _resource(env, *, external=False, identity_type="UserAssigned",
              custom_domains=None, allow_insecure=False):
    """Build a fake Container App resource the doctor knows how to read."""
    ingress = {"external": external, "allowInsecure": allow_insecure}
    if custom_domains is not None:
        ingress["customDomains"] = custom_domains
    props = {
        "configuration": {"ingress": ingress},
        "template": {
            "containers": [
                {"env": [{"name": k, "value": v} for k, v in env.items()]}
            ]
        },
    }
    return types.SimpleNamespace(properties=props, identity={"type": identity_type})


def _bound_domain(name="MydudeMCP.com", cert_id="/subscriptions/x/.../cert"):
    """A customDomains entry bound to a managed certificate (TLS)."""
    return [{"name": name, "certificateId": cert_id, "bindingType": "SniEnabled"}]


#: Env that satisfies every HARD container-app check in PUBLIC posture: same as
#: BASE_ENV but with the host allow-list pinned to the public domain (pinning is
#: HARD-required once the bearer token is the sole gate).
PUBLIC_ENV = dict(BASE_ENV, AZURE_MCP_ALLOWED_HOSTS="MydudeMCP.com")


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


def _check_public(env, *, public_domain="MydudeMCP.com", **kw):
    """Run the container-app check in PUBLIC posture against a canned resource."""
    return D._check_container_app(
        _FakeRmc(_resource(env, **kw)),
        expect_public=True, public_domain=public_domain)


def _managed_env_resource(*, internal):
    """Build a fake managed-environment resource the doctor knows how to read."""
    props = {"vnetConfiguration": {"internal": internal}}
    return types.SimpleNamespace(properties=props)


class _RaisingResources:
    """Fake ``rmc.resources`` whose ``get_by_id`` raises — for not-found paths."""

    def __init__(self, exc):
        self._exc = exc

    def get_by_id(self, resource_id, api_version):
        raise self._exc


class _RaisingRmc:
    def __init__(self, exc):
        self.resources = _RaisingResources(exc)


@contextmanager
def _patched_keyvault(*, secret_value=None, uri_exc=None, get_exc=None):
    """Temporarily replace the doctor's Key Vault helpers with offline fakes.

    ``az.keyvault_uri`` and ``az.kv_get_secret`` are the only live-Azure surface
    in ``_check_keyvault_token``; swap them so the check runs with no network.
    """
    orig_uri = D.az.keyvault_uri
    orig_get = D.az.kv_get_secret

    def fake_uri():
        if uri_exc is not None:
            raise uri_exc
        return "https://fake-vault.vault.azure.net/"

    def fake_get(secret_name, vault_uri):
        if get_exc is not None:
            raise get_exc
        return secret_value

    D.az.keyvault_uri = fake_uri
    D.az.kv_get_secret = fake_get
    try:
        yield
    finally:
        D.az.keyvault_uri = orig_uri
        D.az.kv_get_secret = orig_get


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


# -- container-app HARD checks: PASS when everything is correct ---------------

def test_container_app_passes_when_all_hard_checks_met():
    # BASE_ENV satisfies ENABLE_AZURE_MCP + AZURE_MCP_AUTH_SECRET_NAME; defaults
    # are internal ingress + UserAssigned identity. Should be a clean PASS.
    ok, detail = _check(BASE_ENV)
    assert ok is True
    assert "ingress internal" in detail
    assert "user-assigned identity" in detail


# -- container-app HARD checks: each misconfiguration is a FAIL ---------------

def test_container_app_fails_on_external_ingress():
    ok, detail = _check(BASE_ENV, external=True)
    assert ok is False
    assert "ingress is EXTERNAL" in detail


def test_container_app_fails_when_ingress_external_defaults_true():
    # Missing/empty ingress config must be treated as external (fail-safe).
    res = types.SimpleNamespace(
        properties={
            "configuration": {},
            "template": {"containers": [
                {"env": [{"name": k, "value": v} for k, v in BASE_ENV.items()]}]},
        },
        identity={"type": "UserAssigned"},
    )
    ok, detail = D._check_container_app(_FakeRmc(res))
    assert ok is False
    assert "ingress is EXTERNAL" in detail


def test_container_app_fails_on_non_userassigned_identity():
    ok, detail = _check(BASE_ENV, identity_type="SystemAssigned")
    assert ok is False
    assert "identity is not UserAssigned" in detail
    assert "SystemAssigned" in detail


def test_container_app_fails_when_identity_missing():
    # No identity block at all -> reported as "none" and a FAIL.
    res = _resource(BASE_ENV)
    res.identity = None
    ok, detail = D._check_container_app(_FakeRmc(res))
    assert ok is False
    assert "identity is not UserAssigned" in detail
    assert "none" in detail


def test_container_app_accepts_systemassigned_userassigned_combo():
    # Azure reports both as "SystemAssigned, UserAssigned"; UserAssigned present.
    ok, detail = _check(BASE_ENV, identity_type="SystemAssigned, UserAssigned")
    assert ok is True
    assert "user-assigned identity" in detail


def test_container_app_fails_when_enable_flag_not_true():
    env = dict(BASE_ENV, ENABLE_AZURE_MCP="false")
    ok, detail = _check(env)
    assert ok is False
    assert "ENABLE_AZURE_MCP != true" in detail


def test_container_app_fails_when_enable_flag_missing():
    env = {"AZURE_MCP_AUTH_SECRET_NAME": "azure-mcp-auth-token"}
    ok, detail = _check(env)
    assert ok is False
    assert "ENABLE_AZURE_MCP != true" in detail


def test_container_app_fails_when_auth_secret_name_missing():
    env = {"ENABLE_AZURE_MCP": "true"}
    ok, detail = _check(env)
    assert ok is False
    assert "AZURE_MCP_AUTH_SECRET_NAME unset" in detail


def test_container_app_reports_deploy_apply_gate():
    # default-deny when ALLOW_AZURE_DEPLOY is absent/non-true...
    ok, detail = _check(BASE_ENV)
    assert ok is True
    assert "deploy-apply default-deny" in detail
    # ...and ENABLED when explicitly opted in (still a PASS — it's a report).
    ok2, detail2 = _check(dict(BASE_ENV, ALLOW_AZURE_DEPLOY="true"))
    assert ok2 is True
    assert "deploy-apply ENABLED" in detail2


def test_container_app_unreadable_resource_fails():
    ok, detail = D._check_container_app(_RaisingRmc(RuntimeError("boom")))
    assert ok is False
    assert "container app" in detail
    assert "boom" in detail


# -- managed environment: VNet-internal requirement --------------------------

def test_managed_env_passes_when_vnet_internal():
    ok, detail = D._check_managed_env(_FakeRmc(_managed_env_resource(internal=True)))
    assert ok is True
    assert "vnet-internal" in detail


def test_managed_env_fails_when_not_internal():
    ok, detail = D._check_managed_env(_FakeRmc(_managed_env_resource(internal=False)))
    assert ok is False
    assert "NOT vnet-internal" in detail


def test_managed_env_fails_when_vnet_config_missing():
    # No vnetConfiguration at all -> internal defaults to False -> FAIL.
    res = types.SimpleNamespace(properties={})
    ok, detail = D._check_managed_env(_FakeRmc(res))
    assert ok is False
    assert "NOT vnet-internal" in detail


def test_managed_env_not_found_fails():
    ok, detail = D._check_managed_env(_RaisingRmc(RuntimeError("nope")))
    assert ok is False
    assert "managed environment" in detail
    assert "not found" in detail


# -- Key Vault bearer-token secret presence ----------------------------------

def test_keyvault_token_present_passes():
    with _patched_keyvault(secret_value="s3cr3t-token-value"):
        ok, detail = D._check_keyvault_token("azure-mcp-auth-token")
    assert ok is True
    assert "present" in detail
    assert "value withheld" in detail
    # Defense: the doctor must never echo the secret value.
    assert "s3cr3t-token-value" not in detail


def test_keyvault_token_absent_fails():
    with _patched_keyvault(secret_value=None):
        ok, detail = D._check_keyvault_token("azure-mcp-auth-token")
    assert ok is False
    assert "ABSENT" in detail
    assert "setup_mcp_token.py" in detail


def test_keyvault_token_empty_string_fails():
    # An empty secret is as good as absent — must FAIL.
    with _patched_keyvault(secret_value=""):
        ok, detail = D._check_keyvault_token("azure-mcp-auth-token")
    assert ok is False
    assert "ABSENT" in detail


def test_keyvault_token_uri_error_fails():
    with _patched_keyvault(uri_exc=RuntimeError("no vault")):
        ok, detail = D._check_keyvault_token("azure-mcp-auth-token")
    assert ok is False
    assert "no vault" in detail


def test_keyvault_token_get_error_fails():
    with _patched_keyvault(get_exc=RuntimeError("access denied")):
        ok, detail = D._check_keyvault_token("azure-mcp-auth-token")
    assert ok is False
    assert "access denied" in detail


# -- main(): top-level orchestration -> overall PASS/FAIL + exit code ---------

class _CombinedResources:
    """Fake ``rmc.resources`` that dispatches by resource id.

    ``main`` runs the managed-env and container-app checks through ONE client,
    so ``get_by_id`` must return the right canned resource for each resource
    type (managedEnvironments vs containerApps).
    """

    def __init__(self, env_res, app_res):
        self._env_res = env_res
        self._app_res = app_res

    def get_by_id(self, resource_id, api_version):
        if "managedEnvironments" in resource_id:
            return self._env_res
        if "containerApps" in resource_id:
            return self._app_res
        raise AssertionError("unexpected resource id: %s" % resource_id)


class _CombinedRmc:
    def __init__(self, env_res, app_res):
        self.resources = _CombinedResources(env_res, app_res)


@contextmanager
def _patched_main(*, secret_value="s3cr3t", env=None, internal=True,
                  external=False, identity_type="UserAssigned",
                  custom_domains=None, allow_insecure=False,
                  outputs_exc=None, rmc_exc=None, argv=None):
    """Drive ``main`` fully offline.

    Replaces every Azure surface ``main`` touches: ``az.get_deployment_outputs``,
    the ARM client factory ``_rmc``, and the Key Vault helpers. Also pins
    ``sys.argv`` so argparse doesn't pick up the test runner's args (override via
    ``argv`` to exercise the ``--public``/``--public-domain`` posture).
    """
    app_env = BASE_ENV if env is None else env
    app_res = _resource(app_env, external=external, identity_type=identity_type,
                        custom_domains=custom_domains, allow_insecure=allow_insecure)
    env_res = _managed_env_resource(internal=internal)

    orig_outputs = D.az.get_deployment_outputs
    orig_rmc = D._rmc
    orig_argv = sys.argv

    def fake_outputs():
        if outputs_exc is not None:
            raise outputs_exc
        return {"keyVaultUri": "https://fake-vault.vault.azure.net/"}

    def fake_rmc():
        if rmc_exc is not None:
            raise rmc_exc
        return _CombinedRmc(env_res, app_res)

    D.az.get_deployment_outputs = fake_outputs
    D._rmc = fake_rmc
    sys.argv = argv if argv is not None else ["azure_mcp_doctor"]
    try:
        with _patched_keyvault(secret_value=secret_value):
            yield
    finally:
        D.az.get_deployment_outputs = orig_outputs
        D._rmc = orig_rmc
        sys.argv = orig_argv


def test_main_returns_zero_when_all_checks_pass():
    with _patched_main():
        assert D.main() == 0


def test_main_returns_one_when_keyvault_secret_absent():
    # Every other check passes; only the Key Vault token is missing.
    with _patched_main(secret_value=None):
        assert D.main() == 1


def test_main_returns_one_when_managed_env_not_internal():
    with _patched_main(internal=False):
        assert D.main() == 1


def test_main_returns_one_when_container_app_external():
    with _patched_main(external=True):
        assert D.main() == 1


def test_main_returns_one_when_container_app_misconfigured():
    # A hard container-app env check fails (ENABLE_AZURE_MCP not true).
    with _patched_main(env=dict(BASE_ENV, ENABLE_AZURE_MCP="false")):
        assert D.main() == 1


def test_main_returns_one_when_deployment_outputs_unreadable():
    # Fail before any check runs -> exit 1.
    with _patched_main(outputs_exc=RuntimeError("no outputs")):
        assert D.main() == 1


def test_main_returns_one_when_arm_client_unavailable():
    with _patched_main(rmc_exc=RuntimeError("no creds")):
        assert D.main() == 1


def test_main_passes_with_only_advisory_warning():
    # The host-pinning advisory is non-fatal: unpinned host check must NOT flip
    # the overall verdict to FAIL when every hard check passes.
    with _patched_main(env=BASE_ENV):  # BASE_ENV has no AZURE_MCP_ALLOWED_HOSTS
        assert D.main() == 0


# -- PUBLIC posture: managed environment must be EXTERNAL ---------------------

def test_managed_env_public_passes_when_external():
    ok, detail = D._check_managed_env(
        _FakeRmc(_managed_env_resource(internal=False)), expect_public=True)
    assert ok is True
    assert "external (public posture)" in detail


def test_managed_env_public_fails_when_internal():
    ok, detail = D._check_managed_env(
        _FakeRmc(_managed_env_resource(internal=True)), expect_public=True)
    assert ok is False
    assert "INTERNAL but public posture expected" in detail


# -- PUBLIC posture: container app PASS when external + bound + pinned --------

def test_public_passes_when_external_domain_bound_and_pinned():
    ok, detail = _check_public(PUBLIC_ENV, external=True,
                               custom_domains=_bound_domain())
    assert ok is True
    assert "ingress external (public)" in detail
    assert "custom domain 'MydudeMCP.com' bound (TLS)" in detail
    assert "host check pinned (public)" in detail


# -- PUBLIC posture: each misconfiguration is a HARD FAIL --------------------

def test_public_fails_when_ingress_internal():
    # An internal ingress in public posture is the wrong shape -> FAIL.
    ok, detail = _check_public(PUBLIC_ENV, external=False,
                               custom_domains=_bound_domain())
    assert ok is False
    assert "ingress is INTERNAL" in detail


def test_public_fails_when_allow_insecure_true():
    # TLS is always mandatory — plaintext HTTP must FAIL even in public mode.
    ok, detail = _check_public(PUBLIC_ENV, external=True, allow_insecure=True,
                               custom_domains=_bound_domain())
    assert ok is False
    assert "allowInsecure is TRUE" in detail


def test_public_fails_when_custom_domain_not_bound():
    # External + pinned, but the domain isn't bound to the app -> FAIL.
    ok, detail = _check_public(PUBLIC_ENV, external=True, custom_domains=[])
    assert ok is False
    assert "custom domain 'MydudeMCP.com' is NOT bound" in detail


def test_public_fails_when_custom_domain_bound_without_certificate():
    # Domain present but no certificateId -> no TLS -> FAIL.
    ok, detail = _check_public(
        PUBLIC_ENV, external=True,
        custom_domains=[{"name": "MydudeMCP.com", "certificateId": ""}])
    assert ok is False
    assert "bound without a certificate" in detail


def test_public_fails_when_host_not_pinned():
    # Public posture makes host pinning HARD-required (sole gate is the token).
    env = dict(BASE_ENV)  # no AZURE_MCP_ALLOWED_HOSTS
    ok, detail = _check_public(env, external=True, custom_domains=_bound_domain())
    assert ok is False
    assert "host check NOT pinned to the public domain" in detail


def test_public_fails_when_pinned_but_domain_not_in_allowlist():
    # An allow-list that omits the public domain doesn't actually pin it -> FAIL.
    env = dict(BASE_ENV, AZURE_MCP_ALLOWED_HOSTS="some-other-host.example")
    ok, detail = _check_public(env, external=True, custom_domains=_bound_domain())
    assert ok is False
    assert "host check NOT pinned to the public domain" in detail


def test_public_fails_when_host_check_disabled():
    # Opt-out re-opens the host check — fatal in public posture.
    env = dict(PUBLIC_ENV, AZURE_MCP_DISABLE_HOST_CHECK="true")
    ok, detail = _check_public(env, external=True, custom_domains=_bound_domain())
    assert ok is False
    assert "host check NOT pinned to the public domain" in detail


def test_public_without_domain_still_requires_pinning_and_external():
    # --public with no specific domain: external + generic pinning required, but
    # no custom-domain binding is asserted (phase-1 style check).
    ok, detail = D._check_container_app(
        _FakeRmc(_resource(dict(BASE_ENV, AZURE_MCP_ALLOWED_HOSTS="anything"),
                           external=True)),
        expect_public=True, public_domain=None)
    assert ok is True
    assert "host check pinned (public)" in detail


# -- PUBLIC posture through main(): exit code + posture wiring ----------------

def test_main_public_returns_zero_when_all_public_checks_pass():
    with _patched_main(
            env=PUBLIC_ENV, internal=False, external=True,
            custom_domains=_bound_domain(),
            argv=["azure_mcp_doctor", "--public-domain", "MydudeMCP.com"]):
        assert D.main() == 0


def test_main_public_returns_one_when_env_still_internal():
    # The managed env wasn't recreated as external -> public main must FAIL.
    with _patched_main(
            env=PUBLIC_ENV, internal=True, external=True,
            custom_domains=_bound_domain(),
            argv=["azure_mcp_doctor", "--public-domain", "MydudeMCP.com"]):
        assert D.main() == 1


def test_main_internal_default_fails_against_public_deployment():
    # A genuinely public deployment must NOT pass the default (internal) doctor
    # run — the posture flag is what makes the public shape acceptable.
    with _patched_main(
            env=PUBLIC_ENV, internal=False, external=True,
            custom_domains=_bound_domain()):  # default argv -> internal posture
        assert D.main() == 1


def _run_all():
    tests = [
        test_advisory_warns_when_allowed_hosts_empty,
        test_advisory_warns_when_host_check_explicitly_disabled,
        test_advisory_warns_for_each_truthy_optout_form,
        test_advisory_reports_pinned_when_hosts_set_and_not_disabled,
        test_advisory_pinned_ignores_falsey_disable_value,
        test_advisory_does_not_change_hard_verdict,
        test_container_app_passes_when_all_hard_checks_met,
        test_container_app_fails_on_external_ingress,
        test_container_app_fails_when_ingress_external_defaults_true,
        test_container_app_fails_on_non_userassigned_identity,
        test_container_app_fails_when_identity_missing,
        test_container_app_accepts_systemassigned_userassigned_combo,
        test_container_app_fails_when_enable_flag_not_true,
        test_container_app_fails_when_enable_flag_missing,
        test_container_app_fails_when_auth_secret_name_missing,
        test_container_app_reports_deploy_apply_gate,
        test_container_app_unreadable_resource_fails,
        test_managed_env_passes_when_vnet_internal,
        test_managed_env_fails_when_not_internal,
        test_managed_env_fails_when_vnet_config_missing,
        test_managed_env_not_found_fails,
        test_keyvault_token_present_passes,
        test_keyvault_token_absent_fails,
        test_keyvault_token_empty_string_fails,
        test_keyvault_token_uri_error_fails,
        test_keyvault_token_get_error_fails,
        test_main_returns_zero_when_all_checks_pass,
        test_main_returns_one_when_keyvault_secret_absent,
        test_main_returns_one_when_managed_env_not_internal,
        test_main_returns_one_when_container_app_external,
        test_main_returns_one_when_container_app_misconfigured,
        test_main_returns_one_when_deployment_outputs_unreadable,
        test_main_returns_one_when_arm_client_unavailable,
        test_main_passes_with_only_advisory_warning,
        test_managed_env_public_passes_when_external,
        test_managed_env_public_fails_when_internal,
        test_public_passes_when_external_domain_bound_and_pinned,
        test_public_fails_when_ingress_internal,
        test_public_fails_when_allow_insecure_true,
        test_public_fails_when_custom_domain_not_bound,
        test_public_fails_when_custom_domain_bound_without_certificate,
        test_public_fails_when_host_not_pinned,
        test_public_fails_when_pinned_but_domain_not_in_allowlist,
        test_public_fails_when_host_check_disabled,
        test_public_without_domain_still_requires_pinning_and_external,
        test_main_public_returns_zero_when_all_public_checks_pass,
        test_main_public_returns_one_when_env_still_internal,
        test_main_internal_default_fails_against_public_deployment,
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
