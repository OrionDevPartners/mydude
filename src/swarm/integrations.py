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
