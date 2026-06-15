"""Tests for the Azure deploy driver's fail-loud MCP ingress-posture preflight.

The public custom-domain posture makes the bearer token the SOLE network gate,
so a public deploy MUST be paired with a host pin (DNS-rebinding hardening). The
defence is layered:
  * Bicep never opts the host check out in public mode (covered indirectly by the
    doctor + dev-server tests), and
  * ``deploy.py`` refuses to assemble a billable deploy whose posture would expose
    the server publicly without a host pin.

This file covers the second layer: ``validate_mcp_posture`` in
``infra/mydude/local/deploy.py``. It is a pure function over the ARM parameters
dict, so these tests run completely offline — no Azure, no credentials, no ARM.

Runnable two ways:
  * ``python tests/test_deploy_posture.py``   (standalone; exits non-zero on failure)
  * ``pytest tests/test_deploy_posture.py``    (test_* functions; no plugins needed)
"""
import os
import sys

# deploy.py lives in infra/mydude/local; make it importable.
_DEPLOY_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "infra", "mydude", "local",
)
sys.path.insert(0, _DEPLOY_DIR)

import deploy as DEP  # noqa: E402


# -- helpers ------------------------------------------------------------------

def _params(**kw) -> dict:
    """Wrap raw values into the ARM ``{key: {"value": v}}`` parameter shape."""
    return {k: {"value": v} for k, v in kw.items()}


# -- internal (default) posture: never flagged --------------------------------

def test_internal_default_is_ok():
    # No azureMcp* params at all == Bicep defaults (internal, no domain, no pin).
    assert DEP.validate_mcp_posture({}) == []


def test_internal_explicit_no_pin_is_ok():
    p = _params(azureMcpExternalIngress=False, azureMcpCustomDomain="",
                azureMcpAllowedHosts="")
    assert DEP.validate_mcp_posture(p) == []


def test_internal_with_pin_is_ok():
    # Internal second (hardening) deploy pins the app FQDN — still fine.
    p = _params(azureMcpExternalIngress=False,
                azureMcpAllowedHosts="mcp.internal.azurecontainerapps.io")
    assert DEP.validate_mcp_posture(p) == []


# -- public posture: host pin is HARD-required --------------------------------

def test_public_without_pin_fails_loud():
    p = _params(azureMcpExternalIngress=True, azureMcpAllowedHosts="")
    problems = DEP.validate_mcp_posture(p)
    assert len(problems) == 1
    assert "azureMcpAllowedHosts" in problems[0]


def test_public_with_pin_is_ok():
    p = _params(azureMcpExternalIngress=True, azureMcpAllowedHosts="MydudeMCP.com")
    assert DEP.validate_mcp_posture(p) == []


def test_public_phase1_pinned_without_domain_is_ok():
    # Phase 1: public ingress + host pin but the domain isn't bound yet.
    p = _params(azureMcpExternalIngress=True, azureMcpCustomDomain="",
                azureMcpAllowedHosts="MydudeMCP.com")
    assert DEP.validate_mcp_posture(p) == []


# -- custom-domain invariants -------------------------------------------------

def test_custom_domain_requires_external_ingress():
    p = _params(azureMcpExternalIngress=False,
                azureMcpCustomDomain="MydudeMCP.com",
                azureMcpAllowedHosts="MydudeMCP.com")
    problems = DEP.validate_mcp_posture(p)
    assert len(problems) == 1
    assert "requires azureMcpExternalIngress=true" in problems[0]


def test_custom_domain_must_be_in_allowlist():
    p = _params(azureMcpExternalIngress=True,
                azureMcpCustomDomain="MydudeMCP.com",
                azureMcpAllowedHosts="some-other-host.example")
    problems = DEP.validate_mcp_posture(p)
    assert len(problems) == 1
    assert "must also appear in" in problems[0]


def test_custom_domain_bound_and_pinned_is_ok():
    p = _params(azureMcpExternalIngress=True,
                azureMcpCustomDomain="MydudeMCP.com",
                azureMcpAllowedHosts="MydudeMCP.com")
    assert DEP.validate_mcp_posture(p) == []


def test_custom_domain_match_is_case_insensitive():
    p = _params(azureMcpExternalIngress=True,
                azureMcpCustomDomain="MydudeMCP.com",
                azureMcpAllowedHosts="mydudemcp.com")
    assert DEP.validate_mcp_posture(p) == []


def test_custom_domain_in_multi_host_allowlist_is_ok():
    p = _params(azureMcpExternalIngress=True,
                azureMcpCustomDomain="MydudeMCP.com",
                azureMcpAllowedHosts="MydudeMCP.com, www.mydudemcp.com")
    assert DEP.validate_mcp_posture(p) == []


def test_custom_domain_internal_reports_both_violations():
    # Internal + a custom domain that isn't pinned: two distinct problems.
    p = _params(azureMcpExternalIngress=False,
                azureMcpCustomDomain="MydudeMCP.com",
                azureMcpAllowedHosts="")
    problems = DEP.validate_mcp_posture(p)
    # external+no-hosts is NOT triggered (internal), but domain triggers both
    # "requires external" and "must be in allow-list".
    assert len(problems) == 2


# -- robustness: tolerate odd/missing param shapes ----------------------------

def test_tolerates_non_dict_param_specs():
    # A malformed params dict must not crash the preflight (it should treat the
    # value as absent -> safe internal default).
    assert DEP.validate_mcp_posture({"azureMcpExternalIngress": "oops"}) == []


def test_whitespace_only_domain_is_ignored():
    p = _params(azureMcpExternalIngress=True, azureMcpCustomDomain="   ",
                azureMcpAllowedHosts="MydudeMCP.com")
    assert DEP.validate_mcp_posture(p) == []


# -- standalone runner --------------------------------------------------------

def _run_all() -> int:
    tests = [
        test_internal_default_is_ok,
        test_internal_explicit_no_pin_is_ok,
        test_internal_with_pin_is_ok,
        test_public_without_pin_fails_loud,
        test_public_with_pin_is_ok,
        test_public_phase1_pinned_without_domain_is_ok,
        test_custom_domain_requires_external_ingress,
        test_custom_domain_must_be_in_allowlist,
        test_custom_domain_bound_and_pinned_is_ok,
        test_custom_domain_match_is_case_insensitive,
        test_custom_domain_in_multi_host_allowlist_is_ok,
        test_custom_domain_internal_reports_both_violations,
        test_tolerates_non_dict_param_specs,
        test_whitespace_only_domain_is_ignored,
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
