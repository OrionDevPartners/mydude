import asyncio
import logging
import shlex
import subprocess
from typing import Dict, Any, List

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
    async def browser_open(self, params: Dict[str, Any]) -> str:
        from src.browser.engine import BrowserEngine

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
        summary = "Opened %s via '%s'\nFinal URL: %s\nTitle: %s\n\n%s" % (
            url, result.backend, result.final_url, result.title, (result.text or "")[:1500],
        )
        return summary

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
