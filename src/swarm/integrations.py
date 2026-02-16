import asyncio
import subprocess
from typing import Dict, Any


class Integrations:
    async def git_status(self, params: Dict[str, Any]) -> str:
        return await _run_cmd("git status --porcelain=v1 -b")

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


async def _run_cmd(cmd: str) -> str:
    def run():
        try:
            p = subprocess.run(cmd, shell=True, check=False, capture_output=True, text=True, timeout=30)
            out = (p.stdout or "") + (p.stderr or "")
            return out.strip()[:4000]
        except Exception as e:
            return f"Command failed: {type(e).__name__}: {e}"

    return await asyncio.to_thread(run)
