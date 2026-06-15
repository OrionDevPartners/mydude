"""Azure MCP dev-accelerator SMOKE test: prove the LIVE, bearer-gated MCP
container app really serves its governed tools and rejects bad/absent tokens —
from INSIDE the mydude VNet.

The hermetic tests (tests/test_azure_dev_server.py) mock the broker via an
in-process TestClient, and azure_mcp_doctor.py only validates the deployed ARM
*configuration*. Neither calls the running `/mcp` endpoint. This script closes
that gap: once the container app is deployed (deployAzureMcp=true) it talks to
the real server over the network, so a misconfigured image or broken auth wiring
is caught before any client relies on it.

It runs from INSIDE the Azure VNet (the app ingress is internal-only). From
outside the VNet the connection fails at the network boundary by design — that
failure is the correct, loud signal, not a bug.

Checks (every step fail-loud):
  * outputs        — the live deployment resolves the internal MCP URL.
  * keyvault-token — retrieve the bearer token from Key Vault under the caller's
                     identity (the VALUE is NEVER printed).
  * healthz        — GET /healthz is OPEN (no auth) and returns 200 + ok:true.
  * auth-missing   — POST /mcp with NO token returns 401 + WWW-Authenticate.
  * auth-garbage   — POST /mcp with a junk token returns 401.
  * tools-list     — an MCP session with the real token lists the 6 azure_ tools.
  * deploy-status  — (optional, --invoke-status) calls the read-only
                     azure_deploy_status tool to confirm governance dispatch runs
                     against the real stack.

Usage:
    python3 infra/mydude/local/azure_mcp_smoke.py
    python3 infra/mydude/local/azure_mcp_smoke.py --invoke-status
    python3 infra/mydude/local/azure_mcp_smoke.py --secret-name azure-mcp-auth-token
    python3 infra/mydude/local/azure_mcp_smoke.py --url https://<fqdn>/mcp
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from urllib.parse import urlsplit, urlunsplit

import azure_common as az

DEFAULT_SECRET_NAME = "azure-mcp-auth-token"
HEALTH_PATH = "/healthz"
GARBAGE_TOKEN = "not-a-real-token"  # noqa: S105 - deliberately invalid probe value

#: The governed tools the live server must expose (mirror of the server + tests).
EXPECTED_TOOLS = {
    "azure_cosmos_read", "azure_pg_select", "azure_deploy_status",
    "azure_aoai_complete", "azure_deploy_plan", "azure_deploy_apply",
}


class SmokeError(RuntimeError):
    """Raised when a live smoke check fails — fail loud, never silently pass."""


def _resolve_mcp_url(explicit: str | None) -> str:
    """Return the internal `/mcp` URL from the live deployment outputs.

    Honors an explicit ``--url`` override (handy when running the probe before
    the output is wired), else reads ``azureMcpUrl`` from the ARM outputs and
    fails loud if the stack was deployed with deployAzureMcp=false.
    """
    if explicit:
        url = explicit.strip()
    else:
        outputs = az.get_deployment_outputs()
        url = (outputs.get("azureMcpUrl") or "").strip()
    if not url:
        raise SmokeError(
            "No MCP URL available. Deploy with deployAzureMcp=true (output "
            "'azureMcpUrl') or pass --url https://<app-fqdn>/mcp."
        )
    if url.startswith("NOT_DEPLOYED"):
        raise SmokeError(
            "azureMcpUrl is %r — the MCP container app is not deployed "
            "(deployAzureMcp=false). Redeploy with deployAzureMcp=true." % url
        )
    if not url.startswith("https://"):
        raise SmokeError("Refusing to probe a non-HTTPS MCP URL: %r" % url)
    return url


def _base_of(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _check_health(base_url: str) -> str:
    import httpx

    health_url = base_url + HEALTH_PATH
    with httpx.Client(timeout=20.0) as c:
        r = c.get(health_url)
    if r.status_code != 200:
        raise SmokeError("/healthz returned %d (expected 200)" % r.status_code)
    try:
        body = r.json()
    except Exception as e:  # noqa: BLE001
        raise SmokeError("/healthz body is not JSON: %s" % (str(e)[:120])) from e
    if not isinstance(body, dict) or body.get("ok") is not True:
        raise SmokeError("/healthz did not report ok:true (got %r)" % body)
    return "open, 200, ok:true"


def _check_unauthorized(mcp_url: str, token: str | None) -> str:
    """Assert that a POST to /mcp WITHOUT a valid token is rejected with 401.

    The bearer middleware rejects before the request reaches the MCP app, so the
    body does not matter — any non-health path needs a valid token. We send a
    well-formed MCP-ish POST to be realistic.
    """
    import httpx

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    with httpx.Client(timeout=20.0) as c:
        r = c.post(mcp_url, headers=headers, json=body)
    if r.status_code != 401:
        raise SmokeError(
            "expected 401 for %s token, got %d (the server may be OPEN!)"
            % ("garbage" if token else "missing", r.status_code)
        )
    label = "missing" if token is None else "garbage"
    if token is None and "bearer" not in (r.headers.get("www-authenticate", "").lower()):
        raise SmokeError("401 for missing token lacks WWW-Authenticate: Bearer")
    return "%s token -> 401 (rejected)" % label


async def _check_tools_and_status(mcp_url: str, token: str, *,
                                  invoke_status: bool) -> list[tuple[str, str]]:
    """Open a real MCP session with the bearer token; list tools (+optional call)."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    results: list[tuple[str, str]] = []
    headers = {"Authorization": "Bearer " + token}
    async with streamablehttp_client(mcp_url, headers=headers, timeout=30) as (
            read_stream, write_stream, _get_session_id):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listing = await session.list_tools()
            names = {t.name for t in listing.tools}
            missing = EXPECTED_TOOLS - names
            if missing:
                raise SmokeError(
                    "live server is missing governed tools: %s (got %s)"
                    % (sorted(missing), sorted(names))
                )
            results.append(("tools-list", "all 6 governed tools listed"))

            if invoke_status:
                # azure_deploy_status is read-only: a clean return (success OR a
                # sanitized governed error) proves the contract->policy->broker
                # ->audit dispatch ran end-to-end against the real stack.
                res = await session.call_tool("azure_deploy_status", {})
                if res.isError:
                    text = ""
                    for block in (res.content or []):
                        text = getattr(block, "text", "") or text
                    results.append(
                        ("deploy-status",
                         "dispatch ran; governed error: %s" % (text[:120] or "(no detail)")))
                else:
                    results.append(("deploy-status", "dispatch ran; tool returned OK"))
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="MyDude Azure MCP live smoke test")
    ap.add_argument("--secret-name", default=DEFAULT_SECRET_NAME,
                    help="Key Vault bearer-token secret name (default: %(default)s)")
    ap.add_argument("--url", default=None,
                    help="Override the MCP /mcp URL (else read from ARM outputs).")
    ap.add_argument("--invoke-status", action="store_true",
                    help="Also call the read-only azure_deploy_status tool.")
    args = ap.parse_args()

    print("=== MyDude Azure MCP smoke (live) ===")
    checks: list[tuple[str, str]] = []
    try:
        mcp_url = _resolve_mcp_url(args.url)
        base_url = _base_of(mcp_url)
        checks.append(("outputs", "MCP endpoint resolved (%s)" % base_url))

        token = az.kv_get_secret(args.secret_name, az.keyvault_uri())
        if not token:
            raise SmokeError(
                "bearer secret '%s' is ABSENT/empty in Key Vault — run "
                "setup_mcp_token.py" % args.secret_name)
        checks.append(("keyvault-token",
                       "secret '%s' retrieved (value withheld)" % args.secret_name))

        checks.append(("healthz", _check_health(base_url)))
        checks.append(("auth-missing", _check_unauthorized(mcp_url, None)))
        checks.append(("auth-garbage", _check_unauthorized(mcp_url, GARBAGE_TOKEN)))

        checks.extend(asyncio.run(
            _check_tools_and_status(mcp_url, token, invoke_status=args.invoke_status)))
    except SmokeError as e:
        for name, detail in checks:
            print("   [PASS] %-15s %s" % (name, detail))
        print("   [FAIL] %-15s %s" % ("smoke", str(e)[:200]))
        print("\n!! azure-mcp smoke: FAILED.", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        for name, detail in checks:
            print("   [PASS] %-15s %s" % (name, detail))
        print("   [FAIL] %-15s %s: %s" % ("smoke", type(e).__name__, str(e)[:200]))
        print("\n!! azure-mcp smoke: ERRORED (is this running inside the VNet?).",
              file=sys.stderr)
        return 1

    for name, detail in checks:
        print("   [PASS] %-15s %s" % (name, detail))
    print("\nazure-mcp smoke: ALL PASS — live governed MCP server is up + bearer-gated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
