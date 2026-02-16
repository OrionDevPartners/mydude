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
        return "[STUB] asana_query. Wire Asana PAT/OAuth in broker env; return task graph summaries."

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
