import asyncio
import logging
import shlex
import subprocess
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


def audit_capability(capability, target=None, backend=None, status="ok", detail=None, source=None):
    """Record a capability invocation to the CapabilityAuditLog. Never raises —
    an audit failure must not break the capability or leak details."""
    try:
        from src.database import SessionLocal
        from src.models import CapabilityAuditLog
        db = SessionLocal()
        try:
            db.add(CapabilityAuditLog(
                capability=capability,
                target=(str(target)[:2000] if target is not None else None),
                backend=backend,
                status=status,
                detail=(str(detail)[:2000] if detail is not None else None),
                source=source,
            ))
            db.commit()
        finally:
            db.close()
    except Exception as e:  # pragma: no cover - audit must never break the call
        logger.warning("Failed to write capability audit log: %s", e)


class AuditUnavailable(RuntimeError):
    """Raised when a capability's audit record cannot be durably persisted.

    Destructive / irreversible / billable capabilities REFUSE to act when this is
    raised (governance pillar #4: no ungoverned outbound action). Read-only and
    reversible capabilities keep using the fail-soft :func:`audit_capability`.
    """


def audit_capability_strict(capability, target=None, backend=None, status="ok",
                            detail=None, source=None):
    """Durably record a capability invocation, FAIL-LOUD.

    Unlike :func:`audit_capability` (fail-soft), this RAISES
    :class:`AuditUnavailable` if the record cannot be committed, and returns the
    new row id so a follow-up status can be written after the action completes.
    Use it for irreversible/billable actions that MUST refuse when a durable audit
    trail cannot be guaranteed BEFORE the action is taken.
    """
    try:
        from src.database import SessionLocal
        from src.models import CapabilityAuditLog
        db = SessionLocal()
        try:
            row = CapabilityAuditLog(
                capability=capability,
                target=(str(target)[:2000] if target is not None else None),
                backend=backend,
                status=status,
                detail=(str(detail)[:2000] if detail is not None else None),
                source=source,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return row.id
        finally:
            db.close()
    except Exception as e:  # fail-loud: the caller MUST refuse to proceed
        raise AuditUnavailable("Audit trail unavailable: %s" % (str(e)[:200])) from e


def update_audit_status(audit_id, status, detail=None):
    """Best-effort update of a previously-committed strict-audit row's final status.

    The durable pre-execution record already exists (and the irreversible action
    has by now been taken), so a failure here is logged, never raised — losing the
    final-status update must not turn a successful apply into a reported failure.
    """
    if audit_id is None:
        return
    try:
        from src.database import SessionLocal
        from src.models import CapabilityAuditLog
        db = SessionLocal()
        try:
            row = db.get(CapabilityAuditLog, audit_id)
            if row is not None:
                row.status = status
                if detail is not None:
                    row.detail = str(detail)[:2000]
                db.commit()
        finally:
            db.close()
    except Exception as e:  # pragma: no cover - best-effort status update
        logger.warning("Failed to update capability audit status id=%s: %s", audit_id, e)


class Integrations:
    #: Base64 PNG of the most recent successful browser_open (UI render channel).
    last_browser_screenshot: Optional[str] = None

    async def browser_open(self, params: Dict[str, Any]) -> str:
        from src.browser.engine import BrowserEngine

        self.last_browser_screenshot = None
        url = (params.get("url") or "").strip()
        source = params.get("source")
        if not url:
            audit_capability("browser_open", status="error", detail="missing url", source=source)
            return "No URL provided."
        # The allow-list is enforced at the browser layer BEFORE every navigation
        # hop (including redirects), closing the redirect TOCTOU/SSRF gap a
        # post-navigation check would leave open. Policy stays the source of truth.
        from src.swarm.policy import PolicyEngine
        allow_host = PolicyEngine().is_host_allowed
        engine = BrowserEngine()
        result = await engine.open_page(
            url,
            timeout_ms=int(params.get("timeout_ms", 30000)),
            screenshot=bool(params.get("screenshot", True)),
            max_chars=int(params.get("max_chars", 4000)),
            allow_host=allow_host,
        )
        if getattr(result, "blocked", False):
            audit_capability(
                "browser_open", target=result.final_url or url, backend=result.backend,
                status="blocked", detail=result.error, source=source,
            )
            return "Browser open blocked: %s" % result.error
        if not result.ok:
            audit_capability(
                "browser_open", target=url, backend=",".join(result.attempts) or None,
                status="error", detail=result.error, source=source,
            )
            return "Browser open failed: %s" % result.error
        audit_capability(
            "browser_open", target=url, backend=result.backend, status="ok",
            detail="title=%s" % (result.title or ""), source=source,
        )
        self.last_browser_screenshot = getattr(result, "screenshot_b64", None)
        summary = "Opened %s via '%s'\nFinal URL: %s\nTitle: %s\n\n%s" % (
            url, result.backend, result.final_url, result.title, (result.text or "")[:1500],
        )
        return summary

    async def browser_login(self, params: Dict[str, Any]) -> str:
        """Log into a site and land on its account/billing page.

        SECURITY: ``password`` and ``otp`` arrive in ``params`` and are passed
        straight to the browser layer — they are NEVER written to the audit log
        or returned in the summary. Only the (non-secret) login/account URLs and
        username are referenced.
        """
        from src.browser.engine import BrowserEngine
        from src.swarm.policy import PolicyEngine

        self.last_browser_screenshot = None
        source = params.get("source")
        login_url = (params.get("login_url") or "").strip()
        account_url = (params.get("account_url") or "").strip()
        username = params.get("username") or ""
        password = params.get("password") or ""
        if not login_url:
            audit_capability("browser_login", status="error", detail="missing login_url", source=source)
            return "No login URL provided."
        if not password:
            audit_capability("browser_login", target=login_url, status="error",
                             detail="no stored credential", source=source)
            return ("No stored password for this subscription. Add the account "
                    "password to the vault first.")
        allow_host = PolicyEngine().is_host_allowed
        result = await BrowserEngine().login_page(
            login_url, account_url, username, password,
            otp=params.get("otp"),
            timeout_ms=int(params.get("timeout_ms", 45000)),
            max_chars=int(params.get("max_chars", 4000)),
            allow_host=allow_host,
        )
        return self._finish_interactive("browser_login", result, login_url, source)

    async def browser_cancel(self, params: Dict[str, Any]) -> str:
        """Irreversible: log in and click through the cancel/confirm controls.

        This must only be reached after an explicit user confirmation upstream.
        Same secret-handling rules as :meth:`browser_login`.
        """
        from src.browser.engine import BrowserEngine
        from src.swarm.policy import PolicyEngine

        self.last_browser_screenshot = None
        source = params.get("source")
        login_url = (params.get("login_url") or "").strip()
        account_url = (params.get("account_url") or "").strip()
        username = params.get("username") or ""
        password = params.get("password") or ""
        if not login_url:
            audit_capability("browser_cancel", status="error", detail="missing login_url", source=source)
            return "No login URL provided."
        if not password:
            audit_capability("browser_cancel", target=login_url, status="error",
                             detail="no stored credential", source=source)
            return "No stored password for this subscription."
        allow_host = PolicyEngine().is_host_allowed
        result = await BrowserEngine().cancel_action(
            login_url, account_url, username, password,
            otp=params.get("otp"),
            confirm_texts=params.get("confirm_texts"),
            timeout_ms=int(params.get("timeout_ms", 45000)),
            max_chars=int(params.get("max_chars", 4000)),
            allow_host=allow_host,
        )
        return self._finish_interactive("browser_cancel", result, account_url or login_url, source)

    def _finish_interactive(self, capability, result, target, source):
        """Shared audit + summary for browser_login / browser_cancel.

        Never includes credentials; only URLs, backend, title, and page text.
        """
        # Surface the page snapshot whenever one exists — including the
        # needs-you / blocked outcomes — so the UI can show the user exactly
        # where the flow stopped (the cancel review especially relies on this).
        self.last_browser_screenshot = getattr(result, "screenshot_b64", None)
        if getattr(result, "blocked", False):
            audit_capability(capability, target=result.final_url or target,
                             backend=result.backend, status="blocked",
                             detail=result.error, source=source)
            return "%s blocked: %s" % (capability, result.error)
        if not result.ok:
            err = result.error or ""
            needs_user = any(s in err for s in ("yourself", "CAPTCHA", "one-time code", "SSO"))
            audit_capability(capability, target=target,
                             backend=",".join(result.attempts) or None,
                             status="needs_user" if needs_user else "error",
                             detail=result.error, source=source)
            return "%s did not complete: %s" % (capability, result.error)
        audit_capability(capability, target=target, backend=result.backend,
                         status="ok", detail="title=%s" % (result.title or ""), source=source)
        self.last_browser_screenshot = getattr(result, "screenshot_b64", None)
        return "%s ok via '%s'\nFinal URL: %s\nTitle: %s\n\n%s" % (
            capability, result.backend, result.final_url, result.title,
            (result.text or "")[:1500],
        )

    async def ssh_run(self, params: Dict[str, Any]) -> str:
        from src.bridge.ssh import SSHBridge, SSHBridgeError

        command = (params.get("command") or "").strip()
        source = params.get("source")
        if not command:
            audit_capability("ssh_run", status="error", detail="missing command", source=source)
            return "No command provided."
        bridge = SSHBridge()

        def run():
            return bridge.run_command(command, timeout=int(params.get("timeout", 30)))

        try:
            out = await asyncio.to_thread(run)
            audit_capability("ssh_run", target=command, status="ok", source=source)
            return out
        except SSHBridgeError as e:
            audit_capability("ssh_run", target=command, status="error", detail=str(e), source=source)
            return "SSH bridge error: %s" % e

    async def ssh_read_history(self, params: Dict[str, Any]) -> str:
        from src.bridge.ssh import SSHBridge, SSHBridgeError

        source = params.get("source")
        browser = (params.get("browser") or "chrome").lower()
        limit = int(params.get("limit", 20))
        bridge = SSHBridge()

        def run():
            return bridge.read_browser_history(limit=limit, browser=browser)

        try:
            out = await asyncio.to_thread(run)
            audit_capability("ssh_read_history", target="%s:%d" % (browser, limit), status="ok", source=source)
            return out
        except SSHBridgeError as e:
            audit_capability("ssh_read_history", target=browser, status="error", detail=str(e), source=source)
            return "SSH bridge error: %s" % e

    async def ssh_fetch_code(self, params: Dict[str, Any]) -> str:
        from src.bridge.ssh import SSHBridge, SSHBridgeError

        source = params.get("source")
        within = int(params.get("within_minutes", 10))
        bridge = SSHBridge()

        def run():
            return bridge.fetch_recent_code(within_minutes=within)

        try:
            out = await asyncio.to_thread(run)
            audit_capability("ssh_fetch_code", target="within=%dm" % within, status="ok", source=source)
            return out
        except SSHBridgeError as e:
            audit_capability("ssh_fetch_code", status="error", detail=str(e), source=source)
            return "SSH bridge error: %s" % e

    async def gmail_fetch_code(self, params: Dict[str, Any]) -> str:
        """Read a recent emailed one-time verification code via Gmail (read-only).

        Returns the SMS-style ``Most recent verification code: ...`` string on
        success, an honest ``No ...`` when nothing matches, or a
        ``Gmail bridge error: ...`` string the caller can treat as a failure.
        The email body is never logged — only the extracted code leaves the
        bridge — and the audit row records timing only, never the code.
        """
        from src.bridge.gmail_otp import GmailOtpReader, GmailBridgeError

        source = params.get("source")
        within = int(params.get("within_minutes", 10))
        reader = GmailOtpReader()
        if not reader.available():
            audit_capability("gmail_fetch_code", status="error",
                             detail="not connected", source=source)
            return ("Gmail bridge error: Gmail is not connected. Connect Gmail "
                    "so MyDude can read emailed verification codes.")

        def run():
            return reader.fetch_recent_code(within_minutes=within)

        try:
            out = await asyncio.to_thread(run)
            audit_capability("gmail_fetch_code", target="within=%dm" % within,
                             status="ok", source=source)
            return out
        except GmailBridgeError as e:
            audit_capability("gmail_fetch_code", status="error", detail=str(e), source=source)
            return "Gmail bridge error: %s" % e

    async def imap_read_receipts(self, params: Dict[str, Any]) -> str:
        """Read recent billing/receipt emails over IMAP (read-only).

        Returns a JSON list of ``{from, subject, body, date}`` dicts for the
        discovery layer to parse, or a human-readable error string starting with
        ``Email bridge error:`` / ``Email not configured`` so callers can report
        honestly. Never modifies the mailbox.
        """
        import json
        from src.bridge.email_imap import EmailReceiptReader, EmailBridgeError

        source = params.get("source")
        limit = int(params.get("limit", 50))
        lookback = int(params.get("lookback_days", 365))
        reader = EmailReceiptReader()
        if not reader.available():
            audit_capability("imap_read_receipts", status="error",
                             detail="not configured", source=source)
            return ("Email not configured. Add IMAP_HOST, IMAP_USER and "
                    "IMAP_PASSWORD in the vault.")

        def run():
            return reader.read_receipts(limit=limit, lookback_days=lookback)

        try:
            messages = await asyncio.to_thread(run)
            audit_capability("imap_read_receipts", target="limit=%d" % limit,
                             status="ok", detail="messages=%d" % len(messages),
                             source=source)
            return json.dumps(messages)
        except EmailBridgeError as e:
            audit_capability("imap_read_receipts", status="error", detail=str(e), source=source)
            return "Email bridge error: %s" % e

    async def git_status(self, params: Dict[str, Any]) -> str:
        return await _run_cmd(["git", "status", "--porcelain=v1", "-b"])

    async def terraform_plan(self, params: Dict[str, Any]) -> str:
        env = params.get("env", "dev")
        return f"[STUB] terraform_plan for env={env}. Wire terraform CLI + remote state."

    async def terraform_apply(self, params: Dict[str, Any]) -> str:
        env = params.get("env", "dev")
        return f"[STUB] terraform_apply for env={env}. Blocked unless policy allows + has_plan=True."

    # -- Azure MCP dev-accelerator handlers (Task #186) ------------------
    # Each reaches the provider-agnostic AzureBackends adapter, wraps the
    # blocking Azure/DB call in asyncio.to_thread to keep the event loop free,
    # fails loud on any backend error (never mock data) and audits every
    # invocation. Reached ONLY via the broker after contract + policy gates.

    async def azure_cosmos_read(self, params: Dict[str, Any]) -> str:
        import json
        from src.mcp.azure_backends import AzureBackends, AzureBackendError
        source = params.get("source")
        database = (params.get("database") or "").strip()
        container = (params.get("container") or "").strip()
        query = params.get("query") or ""
        parameters = params.get("parameters")
        max_items = params.get("max_items")
        backends = AzureBackends()

        def run():
            kwargs: Dict[str, Any] = {}
            if max_items is not None:
                kwargs["max_items"] = int(max_items)
            return backends.cosmos_read(database, container, query,
                                        parameters=parameters, **kwargs)

        try:
            result = await asyncio.to_thread(run)
            audit_capability("azure_cosmos_read", target="%s/%s" % (database, container),
                             backend="cosmos", status="ok",
                             detail="count=%d" % result.get("count", 0), source=source)
            return json.dumps({"ok": True, **result})
        except AzureBackendError as e:
            audit_capability("azure_cosmos_read", target="%s/%s" % (database, container),
                             backend="cosmos", status="error", detail=str(e)[:500],
                             source=source)
            return json.dumps({"ok": False, "error": str(e)})

    async def azure_pg_select(self, params: Dict[str, Any]) -> str:
        import json
        from src.mcp.azure_backends import AzureBackends, AzureBackendError
        source = params.get("source")
        db_key = (params.get("db_key") or "").strip()
        sql = params.get("sql") or ""
        sql_params = params.get("params")
        max_rows = params.get("max_rows")
        backends = AzureBackends()

        def run():
            kwargs: Dict[str, Any] = {}
            if max_rows is not None:
                kwargs["max_rows"] = int(max_rows)
            return backends.pg_select(db_key, sql, params=sql_params, **kwargs)

        try:
            result = await asyncio.to_thread(run)
            audit_capability("azure_pg_select", target=db_key, backend="postgres",
                             status="ok", detail="rowcount=%d" % result.get("rowcount", 0),
                             source=source)
            return json.dumps({"ok": True, **result})
        except AzureBackendError as e:
            audit_capability("azure_pg_select", target=db_key, backend="postgres",
                             status="error", detail=str(e)[:500], source=source)
            return json.dumps({"ok": False, "error": str(e)})

    async def azure_deploy_status(self, params: Dict[str, Any]) -> str:
        import json
        from src.mcp.azure_backends import AzureBackends, AzureBackendError
        source = params.get("source")
        backends = AzureBackends()
        try:
            result = await asyncio.to_thread(backends.deploy_status)
            audit_capability("azure_deploy_status", target=result.get("deployment"),
                             backend="arm", status="ok",
                             detail="state=%s" % result.get("state"), source=source)
            return json.dumps({"ok": True, **result})
        except AzureBackendError as e:
            audit_capability("azure_deploy_status", backend="arm", status="error",
                             detail=str(e)[:500], source=source)
            return json.dumps({"ok": False, "error": str(e)})

    async def azure_aoai_complete(self, params: Dict[str, Any]) -> str:
        """GOVERNED completion — routes through the swarm service (full envelope).
        There is deliberately NO raw Azure OpenAI passthrough (pillar #4)."""
        import json
        from src.mcp.azure_backends import AzureBackends, AzureBackendError
        source = params.get("source")
        prompt = (params.get("prompt") or "").strip()
        domain = params.get("domain") or "general"
        team = params.get("team") or "default"
        if not prompt:
            audit_capability("azure_aoai_complete", status="error",
                             detail="missing prompt", source=source)
            return json.dumps({"ok": False, "error": "prompt is required."})
        backends = AzureBackends()
        try:
            result = await backends.aoai_complete(prompt, domain=domain, team=team)
            audit_capability("azure_aoai_complete", target="domain=%s" % domain,
                             backend="governed_swarm", status="ok",
                             detail="governed completion returned", source=source)
            return json.dumps({"ok": True, "result": result}, default=str)
        except AzureBackendError as e:
            audit_capability("azure_aoai_complete", target="domain=%s" % domain,
                             backend="governed_swarm", status="error",
                             detail=str(e)[:500], source=source)
            return json.dumps({"ok": False, "error": str(e)})
        except Exception as e:  # noqa: BLE001 - surface (fail loud), never mock
            audit_capability("azure_aoai_complete", target="domain=%s" % domain,
                             backend="governed_swarm", status="error",
                             detail=str(e)[:500], source=source)
            return json.dumps({"ok": False,
                               "error": "Governed completion failed: %s" % str(e)[:300]})

    async def memory_recall(self, params: Dict[str, Any]) -> str:
        """READ-ONLY recall from MyDude's long-term governed memory.

        Surfaces semantically related entries to an MCP client. The substrate is
        synchronous, so the read runs off the event loop. Output is sanitized to
        a stable, non-secret projection — private (local-only digital-twin)
        entries are NEVER returned, and arbitrary metadata is dropped (only the
        count of filtered private entries is reported). Fails loud on error and
        never mocks (pillars #1, #4)."""
        import json
        source = params.get("source")
        query = (params.get("query") or "").strip()
        if not query:
            audit_capability("memory_recall", status="error",
                             detail="missing query", source=source)
            return json.dumps({"ok": False, "error": "query is required."})

        top_k = params.get("top_k")
        try:
            top_k = int(top_k) if top_k is not None else 5
        except (TypeError, ValueError):
            top_k = 5
        top_k = max(1, min(top_k, 50))

        category = params.get("category") or None
        min_confidence = params.get("min_confidence")
        try:
            min_confidence = float(min_confidence) if min_confidence is not None else 0.3
        except (TypeError, ValueError):
            min_confidence = 0.3

        try:
            from src.memory.substrate import get_substrate
            substrate = get_substrate()
            entries = await asyncio.to_thread(
                substrate.recall, query, top_k=top_k,
                category=category, min_confidence=min_confidence,
            )
            results: List[Dict[str, Any]] = []
            private_filtered = 0
            for e in (entries or []):
                meta = getattr(e, "metadata", None) or {}
                if isinstance(meta, dict) and meta.get("private"):
                    private_filtered += 1
                    continue  # never expose local-only digital-twin memory
                created = getattr(e, "created_at", None)
                results.append({
                    "memory_id": getattr(e, "memory_id", None),
                    "content": str(getattr(e, "content", "") or "")[:1000],
                    "category": getattr(e, "category", None),
                    "confidence": getattr(e, "confidence", None),
                    "verified": getattr(e, "verified", None),
                    "source": getattr(e, "source", None),
                    "created_at": created.isoformat() if hasattr(created, "isoformat") else (
                        str(created) if created is not None else None),
                })
            audit_capability("memory_recall", target="top_k=%s" % top_k,
                             backend="memory_substrate", status="ok",
                             detail="returned %d (filtered %d private)" % (
                                 len(results), private_filtered),
                             source=source)
            return json.dumps({
                "ok": True, "results": results, "count": len(results),
                "private_filtered_count": private_filtered,
            }, default=str)
        except Exception as e:  # noqa: BLE001 - surface (fail loud), never mock
            audit_capability("memory_recall", status="error",
                             detail=str(e)[:500], source=source)
            return json.dumps({"ok": False,
                               "error": "Memory recall failed: %s" % str(e)[:300]})

    async def azure_deploy_plan(self, params: Dict[str, Any]) -> str:
        """PLAN phase: ARM what-if (no resources, no cost) -> short-lived signed
        token binding the approved plan (and its params fingerprint) to apply."""
        import json
        from src.mcp.azure_backends import (
            AzureBackends, AzureBackendError, compute_plan_hash, sign_plan_token,
            PLAN_TOKEN_TTL_SECONDS, AZURE_DEPLOY_CONFIRM_PHRASE,
        )
        source = params.get("source")
        actor = params.get("actor") or source
        backends = AzureBackends()
        try:
            plan = await asyncio.to_thread(backends.deploy_what_if)
            plan_hash = compute_plan_hash(plan.get("changes"))
            # Guarantee a DURABLE approval record BEFORE minting a token that
            # authorizes a billable apply (pillar #4). If the audit trail is down,
            # refuse to issue a token rather than approve an unauditable plan.
            try:
                audit_capability_strict(
                    "azure_deploy_plan", target="mydude-stack", backend="arm",
                    status="ok",
                    detail="change_count=%s plan_hash=%s" % (
                        plan.get("change_count"), plan_hash[:12]),
                    source=source)
            except AuditUnavailable as ae:
                return json.dumps({
                    "ok": False,
                    "error": "Refusing to issue a deploy plan token: %s" % str(ae),
                })
            token = sign_plan_token(plan_hash=plan_hash,
                                    params_hash=plan.get("params_hash"),
                                    template_hash=plan.get("template_hash"),
                                    actor=actor, source=source)
            # params_hash + template_hash are bound INSIDE the token only — never
            # returned to a caller.
            return json.dumps({
                "ok": True,
                "changes": plan.get("changes"),
                "change_count": plan.get("change_count"),
                "template_resource_count": plan.get("template_resource_count"),
                "plan_hash": plan_hash,
                "plan_token": token,
                "expires_in": PLAN_TOKEN_TTL_SECONDS,
                "confirm_phrase": AZURE_DEPLOY_CONFIRM_PHRASE,
            })
        except AzureBackendError as e:
            audit_capability("azure_deploy_plan", target="mydude-stack", backend="arm",
                             status="error", detail=str(e)[:500], source=source)
            return json.dumps({"ok": False, "error": str(e)})

    async def azure_deploy_apply(self, params: Dict[str, Any]) -> str:
        """APPLY phase (BILLABLE): verify the plan token + exact plan hash +
        confirm phrase, then submit create_or_update with the params fingerprint
        bound at plan time. Fails loud on tamper/expiry/drift; audits every path."""
        import json
        from src.mcp.azure_backends import (
            AzureBackends, AzureBackendError, verify_plan_token,
            AZURE_DEPLOY_CONFIRM_PHRASE,
        )
        source = params.get("source")
        plan_token = params.get("plan_token") or ""
        provided_plan_hash = params.get("plan_hash") or ""
        confirm = (params.get("confirm") or "").strip()
        # Defense in depth: the contract already enforces the confirm phrase, but
        # re-check here so a direct (non-broker) call path can never apply
        # without the exact, explicit confirmation.
        if confirm != AZURE_DEPLOY_CONFIRM_PHRASE:
            audit_capability("azure_deploy_apply", target="mydude-stack", backend="arm",
                             status="blocked", detail="missing/incorrect confirm phrase",
                             source=source)
            return json.dumps({"ok": False,
                               "error": "Exact confirm phrase required to apply."})
        try:
            payload = verify_plan_token(plan_token)
        except AzureBackendError as e:
            audit_capability("azure_deploy_apply", target="mydude-stack", backend="arm",
                             status="blocked", detail=str(e)[:500], source=source)
            return json.dumps({"ok": False, "error": str(e)})
        if payload.get("plan_hash") != provided_plan_hash:
            audit_capability("azure_deploy_apply", target="mydude-stack", backend="arm",
                             status="blocked",
                             detail="plan_hash mismatch (token vs supplied)", source=source)
            return json.dumps({"ok": False,
                               "error": "plan_hash does not match the approved plan "
                                        "token; re-plan required."})
        expected_params_hash = payload.get("params_hash")
        expected_plan_hash = payload.get("plan_hash")
        expected_template_hash = payload.get("template_hash")
        # Guaranteed pre-execution audit (pillar #4): a billable, irreversible apply
        # must REFUSE if a durable record of the attempt cannot be written BEFORE we
        # submit. The fail-soft audit_capability() can silently drop a write, so the
        # destructive path uses the strict variant and refuses on AuditUnavailable.
        try:
            audit_id = audit_capability_strict(
                "azure_deploy_apply", target="mydude-stack", backend="arm",
                status="submitting",
                detail="confirmed apply plan_hash=%s" % (provided_plan_hash[:12]),
                source=source)
        except AuditUnavailable as ae:
            # Best-effort note of the refusal (fail-soft); the apply does NOT run.
            audit_capability("azure_deploy_apply", target="mydude-stack", backend="arm",
                             status="blocked",
                             detail="refused: audit trail unavailable", source=source)
            return json.dumps({
                "ok": False,
                "error": "Refusing to apply: a durable audit record could not be "
                         "guaranteed (%s)." % str(ae),
            })

        backends = AzureBackends()

        def run():
            return backends.deploy_apply(expected_params_hash=expected_params_hash,
                                         expected_plan_hash=expected_plan_hash,
                                         expected_template_hash=expected_template_hash,
                                         no_wait=True)

        try:
            result = await asyncio.to_thread(run)
            update_audit_status(audit_id, "ok",
                                "submitted state=%s" % result.get("state"))
            return json.dumps({"ok": True, **result})
        except AzureBackendError as e:
            update_audit_status(audit_id, "error", str(e)[:500])
            return json.dumps({"ok": False, "error": str(e)})

    async def asana_query(self, params: Dict[str, Any]) -> str:
        import os
        token = os.getenv("ASANA_PAT")
        if not token:
            return "Asana not configured. Add ASANA_PAT to 1Password vault."
        from src.asana_client import AsanaClient
        client = AsanaClient(token)
        action = params.get("action", "list_projects")
        if action == "list_projects":
            ws = client.get_default_workspace()
            if not ws:
                return "No Asana workspace found."
            projects = client.get_projects(ws["gid"])
            return "\n".join(f"- {p['name']} (gid: {p['gid']})" for p in projects) or "No projects."
        elif action == "create_task":
            project_gid = params.get("project_gid")
            if not project_gid:
                ws = client.get_default_workspace()
                if not ws:
                    return "No Asana workspace found."
                proj = client.get_default_project(ws["gid"])
                if not proj:
                    return "Could not find or create Asana project."
                project_gid = proj["gid"]
            name = params.get("name", "Untitled Task")
            notes = params.get("notes", "")
            due_on = params.get("due_on")
            result = client.create_task(project_gid, name, notes, due_on)
            if "error" in result:
                return f"Failed to create task: {result['error']}"
            return f"Created task: {result.get('name', name)} (gid: {result.get('gid', 'unknown')})"
        return f"Unknown asana action: {action}"

    async def op_read_scoped(self, params: Dict[str, Any]) -> str:
        item = params.get("item", "unknown")
        return f"[STUB] 1Password scoped read for item={item} (no raw secret returned)."

    async def bot_spawn(self, params: Dict[str, Any]) -> str:
        """Governed bot spawn. Called only by broker after contract+policy validation."""
        import json
        from src.fleet.spawner import _do_spawn
        source = params.get("source")
        result = await _do_spawn(params)
        status = "ok" if result.get("ok") else "error"
        audit_capability(
            "bot_spawn",
            target=params.get("name"),
            status=status,
            detail=(result.get("error") if not result.get("ok") else f"bot_id={result.get('bot_id')}"),
            source=source,
        )
        return json.dumps(result)

    async def calendly_book(self, params: Dict[str, Any]) -> str:
        """Governed meeting booking via Calendly. Called only by the broker after
        contract+policy validation. Mints a single-use scheduling link for a
        qualified prospect; the booking action is recorded in the capability
        audit trail. Provider errors fail loud (no mock link)."""
        import asyncio
        import json
        from src.sales.booking import (
            book_meeting, SalesNotConfigured, SalesAuthError, SalesProviderError,
        )
        source = params.get("source")
        target = str(params.get("conversation_id") or params.get("prospect") or "")
        try:
            # book_meeting does blocking httpx I/O — keep the event loop free.
            result = await asyncio.to_thread(book_meeting, params)
            audit_capability(
                "calendly_book",
                target=target or None,
                backend="calendly",
                status="ok",
                detail=f"booking_url issued (source={result.get('source')})",
                source=source,
            )
            return json.dumps(result)
        except (SalesNotConfigured, SalesAuthError, SalesProviderError) as e:
            audit_capability(
                "calendly_book",
                target=target or None,
                backend="calendly",
                status="error",
                detail=str(e)[:500],
                source=source,
            )
            return json.dumps({"ok": False, "error": str(e)})

    async def fleet_provision_plan(self, params: Dict[str, Any]) -> str:
        """Governed provisioning plan. Called only by broker after contract+policy validation."""
        import json
        from src.fleet.provisioner import _do_create_job
        source = params.get("source")
        result = await _do_create_job(params)
        status = "ok" if result.get("ok") else "error"
        audit_capability(
            "fleet_provision_plan",
            target=params.get("resource_type"),
            status=status,
            detail=(result.get("error") if not result.get("ok") else f"job_id={result.get('job_id')}"),
            source=source,
        )
        return json.dumps(result)

    async def fleet_provision_approve(self, params: Dict[str, Any]) -> str:
        """Governed provisioning apply. Called only by broker after contract+policy validation."""
        import json
        from src.fleet.provisioner import _do_apply_job
        source = params.get("source")
        result = await _do_apply_job(params)
        status = "ok" if result.get("ok") else "error"
        audit_capability(
            "fleet_provision_approve",
            target=str(params.get("job_id")),
            status=status,
            detail=(result.get("error") if not result.get("ok") else f"resource={result.get('resource_id')}"),
            source=source,
        )
        return json.dumps(result)

    async def voice_synthesize(self, params: Dict[str, Any]) -> str:
        """Governed text-to-speech. Called only by the broker after contract+policy
        validation. Synthesizes via the provider-agnostic voice facade and parks
        the audio behind a short-lived token (for telephony playback / previews).
        Provider errors fail loud (no silent/mock audio) and are audited."""
        import json
        from src.avatar.voice import (
            synthesize, AvatarNotConfigured,
        )
        from src.avatar.providers import AvatarAuthError, AvatarProviderError
        from src.telephony.audio_store import store_audio
        source = params.get("source")
        text = (params.get("text") or "").strip()
        voice_id = (params.get("voice_id") or "").strip()
        governed = params.get("governed") is True
        decision_trace_id = params.get("decision_trace_id")
        if not text or not voice_id:
            audit_capability("voice_synthesize", status="error",
                             detail="missing text or voice_id", source=source)
            return json.dumps({"ok": False, "error": "text and voice_id are required."})
        # Proof-of-governance gate (pillar #4): TTS must only voice text that has
        # already passed a governance gate. The contract requires governed=True,
        # but re-check here so a direct (non-broker) call path can never synthesize
        # arbitrary ungoverned text. Rejections are audited as blocked.
        if not governed:
            audit_capability("voice_synthesize", target="voice=%s" % voice_id,
                             status="blocked",
                             detail="ungoverned synthesis rejected (governed flag not set)",
                             source=source)
            return json.dumps({
                "ok": False,
                "error": "voice_synthesize requires governed text "
                         "(governed=True with a decision trace).",
            })
        try:
            # synthesize + store both do blocking I/O — keep the loop free.
            audio, content_type = await asyncio.to_thread(synthesize, text, voice_id)
            token = await asyncio.to_thread(
                store_audio, audio, content_type, params.get("call_session_id")
            )
            _detail = "bytes=%d" % len(audio)
            if decision_trace_id:
                _detail += " trace=%s" % decision_trace_id
            audit_capability("voice_synthesize", target="voice=%s" % voice_id,
                             backend="elevenlabs", status="ok",
                             detail=_detail, source=source)
            return json.dumps({"ok": True, "audio_token": token,
                               "content_type": content_type, "bytes": len(audio)})
        except (AvatarNotConfigured, AvatarAuthError, AvatarProviderError) as e:
            audit_capability("voice_synthesize", target="voice=%s" % voice_id,
                             backend="elevenlabs", status="error",
                             detail=str(e)[:500], source=source)
            return json.dumps({"ok": False, "error": str(e)})

    async def telephony_place_call(self, params: Dict[str, Any]) -> str:
        """Governed outbound call. Called only by the broker after contract+policy
        validation. Creates a CallSession, asks the provider to dial, and records
        the provider call SID. Provider errors fail loud (no mock SID)."""
        import json
        from datetime import datetime
        from src.database import SessionLocal
        from src.models import Bot, CallSession
        from src.telephony.facade import (
            place_call, public_base_url,
            TelephonyNotConfigured, TelephonyAuthError, TelephonyProviderError,
        )
        source = params.get("source")
        bot_id = params.get("bot_id")
        to_number = (params.get("to_number") or "").strip()

        db = SessionLocal()
        try:
            bot = db.query(Bot).filter(Bot.id == bot_id).first()
            if not bot:
                audit_capability("telephony_place_call", status="error",
                                 detail="unknown bot_id=%s" % bot_id, source=source)
                return json.dumps({"ok": False, "error": "Unknown bot_id %s." % bot_id})
            from_number = (params.get("from_number") or bot.phone_number or "").strip() or None
            cs = CallSession(
                bot_id=bot.id, provider="twilio", direction="outbound",
                status="queued", to_number=to_number, from_number=from_number,
            )
            db.add(cs)
            db.commit()
            call_session_id = cs.id
        finally:
            db.close()

        # Webhooks must be absolute, externally-reachable URLs (fail loud if not).
        try:
            base = public_base_url()
        except TelephonyNotConfigured as e:
            _mark_call_failed(call_session_id, str(e))
            audit_capability("telephony_place_call", target=to_number, status="error",
                             detail=str(e)[:500], source=source)
            return json.dumps({"ok": False, "error": str(e), "call_session_id": call_session_id})
        answer_url = "%s/api/telephony/voice?cs=%d" % (base, call_session_id)
        status_cb = "%s/api/telephony/status?cs=%d" % (base, call_session_id)

        try:
            res = await asyncio.to_thread(
                place_call, to_number, answer_url,
                from_number, status_cb,
            )
        except (TelephonyNotConfigured, TelephonyAuthError, TelephonyProviderError) as e:
            _mark_call_failed(call_session_id, str(e))
            audit_capability("telephony_place_call", target=to_number, backend="twilio",
                             status="error", detail=str(e)[:500], source=source)
            return json.dumps({"ok": False, "error": str(e), "call_session_id": call_session_id})

        # Record the provider SID + initial status on the session.
        db = SessionLocal()
        try:
            cs = db.query(CallSession).filter(CallSession.id == call_session_id).first()
            if cs:
                cs.provider_call_sid = res.get("sid")
                cs.status = (res.get("status") or "queued")
                cs.started_at = datetime.utcnow()
                db.commit()
        finally:
            db.close()
        audit_capability("telephony_place_call", target=to_number, backend="twilio",
                         status="ok", detail="sid=%s" % res.get("sid"), source=source)
        return json.dumps({"ok": True, "call_sid": res.get("sid"),
                           "status": res.get("status"), "call_session_id": call_session_id})

    async def telephony_receive_call(self, params: Dict[str, Any]) -> str:
        """Governed inbound call acceptance. Called only by the broker after
        contract+policy validation. Routes the dialed number to the owning bot and
        opens a CallSession. Fails loud (honestly) when no bot owns the number."""
        import json
        from datetime import datetime
        from src.database import SessionLocal
        from src.models import Bot, CallSession
        source = params.get("source")
        to_number = (params.get("to_number") or "").strip()
        from_number = (params.get("from_number") or "").strip() or None
        call_sid = (params.get("call_sid") or "").strip() or None

        db = SessionLocal()
        try:
            bot = db.query(Bot).filter(Bot.phone_number == to_number).first() if to_number else None
            if not bot:
                audit_capability("telephony_receive_call", target=to_number, backend="twilio",
                                 status="error", detail="no bot owns this number", source=source)
                return json.dumps({"ok": False,
                                   "error": "No bot is assigned to %s." % (to_number or "(unknown)")})
            cs = CallSession(
                bot_id=bot.id, provider="twilio", direction="inbound",
                status="in_progress", to_number=to_number, from_number=from_number,
                provider_call_sid=call_sid, started_at=datetime.utcnow(),
            )
            db.add(cs)
            db.commit()
            call_session_id = cs.id
            bot_id = bot.id
        finally:
            db.close()
        audit_capability("telephony_receive_call", target=to_number, backend="twilio",
                         status="ok", detail="bot_id=%d sid=%s" % (bot_id, call_sid or ""),
                         source=source)
        return json.dumps({"ok": True, "call_session_id": call_session_id, "bot_id": bot_id})

    async def telephony_turn(self, params: Dict[str, Any]) -> str:
        """Governed single conversation turn on a live call. Called only by the
        broker after contract+policy validation. Delegates to the telephony
        conversation engine, which governs the reply (CS/HR), writes a
        DecisionTrace, and synthesizes the spoken line."""
        import json
        from src.telephony.conversation import run_turn
        source = params.get("source")
        call_session_id = params.get("call_session_id")
        caller_text = params.get("caller_text")
        try:
            result = await run_turn(call_session_id, caller_text=caller_text)
        except Exception as e:  # noqa: BLE001 — surface as a governed error, audited
            audit_capability("telephony_turn", target=str(call_session_id), status="error",
                             detail=str(e)[:500], source=source)
            return json.dumps({"ok": False, "error": str(e)})
        audit_capability(
            "telephony_turn", target=str(call_session_id), status="ok",
            detail="degraded=%s end=%s trace=%s" % (
                result.get("degraded"), result.get("end_call"), result.get("trace_id")),
            source=source,
        )
        return json.dumps(result)


def _mark_call_failed(call_session_id, error):
    """Mark a CallSession failed with an error reason. Best-effort; never raises."""
    try:
        from datetime import datetime
        from src.database import SessionLocal
        from src.models import CallSession
        db = SessionLocal()
        try:
            cs = db.query(CallSession).filter(CallSession.id == call_session_id).first()
            if cs:
                cs.status = "failed"
                cs.error = str(error)[:2000]
                cs.ended_at = datetime.utcnow()
                db.commit()
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to mark call %s failed: %s", call_session_id, e)


async def _run_cmd(cmd: List[str]) -> str:
    def run():
        try:
            # SECURITY: shell=False with an explicit argument list (no shell
            # interpolation); all callers are broker/policy-gated and pass static
            # command lists. Bounded by a 30s timeout and truncated output.
            p = subprocess.run(cmd, shell=False, check=False, capture_output=True, text=True, timeout=30)
            out = (p.stdout or "") + (p.stderr or "")
            return out.strip()[:4000]
        except Exception as e:
            return f"Command failed: {type(e).__name__}: {e}"

    return await asyncio.to_thread(run)
