import logging
import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://app.asana.com/api/1.0"


class AsanaClient:
    def __init__(self, token: str):
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: dict = None) -> dict:
        try:
            resp = requests.get(
                f"{BASE_URL}{path}",
                headers=self.headers,
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"Asana API HTTP error on GET {path}: {e}")
            return {"error": str(e), "status": getattr(e.response, "status_code", None)}
        except Exception as e:
            logger.error(f"Asana API error on GET {path}: {e}")
            return {"error": str(e)}

    def _post(self, path: str, data: dict) -> dict:
        try:
            resp = requests.post(
                f"{BASE_URL}{path}",
                headers=self.headers,
                json={"data": data},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"Asana API HTTP error on POST {path}: {e}")
            return {"error": str(e), "status": getattr(e.response, "status_code", None)}
        except Exception as e:
            logger.error(f"Asana API error on POST {path}: {e}")
            return {"error": str(e)}

    def get_workspaces(self) -> list:
        result = self._get("/workspaces")
        if "error" in result:
            return []
        return result.get("data", [])

    def get_projects(self, workspace_gid: str) -> list:
        result = self._get(f"/workspaces/{workspace_gid}/projects")
        if "error" in result:
            return []
        return result.get("data", [])

    def create_task(self, project_gid: str, name: str, notes: str = "", due_on: str = None) -> dict:
        data = {
            "name": name,
            "notes": notes,
            "projects": [project_gid],
        }
        if due_on:
            data["due_on"] = due_on
        result = self._post("/tasks", data)
        if "error" in result:
            return result
        return result.get("data", result)

    def create_tasks_batch(self, project_gid: str, tasks: list) -> list:
        results = []
        for task_info in tasks:
            name = task_info.get("name", "Untitled Task")
            notes = task_info.get("notes", "")
            due_on = task_info.get("due_on")
            result = self.create_task(project_gid, name, notes, due_on)
            results.append(result)
        return results

    def get_default_workspace(self) -> dict:
        workspaces = self.get_workspaces()
        if workspaces:
            return workspaces[0]
        return {}

    def get_default_project(self, workspace_gid: str) -> dict:
        projects = self.get_projects(workspace_gid)
        if projects:
            return projects[0]
        result = self._post("/projects", {
            "name": "Bot Extractions",
            "workspace": workspace_gid,
        })
        if "error" in result:
            return {}
        return result.get("data", {})
