import os
import json
import asyncio
import logging
import requests

logger = logging.getLogger(__name__)

class IntegrationService:
    """Manages external integrations (Linear, GitHub, Slack, Discord, Calendar)."""

    @staticmethod
    async def send_slack(webhook_url: str, message: str) -> dict:
        try:
            def _send():
                resp = requests.post(webhook_url, json={"text": message}, timeout=10)
                return {"ok": resp.status_code == 200, "status": resp.status_code}
            return await asyncio.to_thread(_send)
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    @staticmethod
    async def send_discord(webhook_url: str, message: str) -> dict:
        try:
            def _send():
                resp = requests.post(webhook_url, json={"content": message[:2000]}, timeout=10)
                return {"ok": resp.status_code in (200, 204), "status": resp.status_code}
            return await asyncio.to_thread(_send)
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    @staticmethod
    async def create_github_issue(token: str, repo: str, title: str, body: str = "") -> dict:
        try:
            def _create():
                headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
                data = {"title": title, "body": body}
                resp = requests.post(f"https://api.github.com/repos/{repo}/issues", headers=headers, json=data, timeout=15)
                if resp.status_code == 201:
                    return {"ok": True, "url": resp.json().get("html_url", "")}
                return {"ok": False, "status": resp.status_code, "error": resp.text[:200]}
            return await asyncio.to_thread(_create)
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    @staticmethod
    async def list_github_issues(token: str, repo: str, state: str = "open", limit: int = 10) -> dict:
        try:
            def _list():
                headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
                resp = requests.get(f"https://api.github.com/repos/{repo}/issues?state={state}&per_page={limit}", headers=headers, timeout=15)
                if resp.status_code == 200:
                    issues = resp.json()
                    return {"ok": True, "issues": [{"number": i["number"], "title": i["title"], "state": i["state"], "url": i["html_url"]} for i in issues]}
                return {"ok": False, "status": resp.status_code}
            return await asyncio.to_thread(_list)
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    @staticmethod
    async def create_linear_issue(api_key: str, title: str, description: str = "", team_id: str = "") -> dict:
        try:
            def _create():
                headers = {"Authorization": api_key, "Content-Type": "application/json"}
                query = '''mutation IssueCreate($input: IssueCreateInput!) {
                    issueCreate(input: $input) {
                        success
                        issue { id identifier title url }
                    }
                }'''
                variables = {"input": {"title": title, "description": description}}
                if team_id:
                    variables["input"]["teamId"] = team_id
                resp = requests.post("https://api.linear.app/graphql", headers=headers, json={"query": query, "variables": variables}, timeout=15)
                data = resp.json()
                if "data" in data and data["data"].get("issueCreate", {}).get("success"):
                    issue = data["data"]["issueCreate"]["issue"]
                    return {"ok": True, "id": issue.get("identifier", ""), "url": issue.get("url", "")}
                return {"ok": False, "error": json.dumps(data.get("errors", []))[:200]}
            return await asyncio.to_thread(_create)
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    @staticmethod
    async def list_linear_issues(api_key: str, limit: int = 10) -> dict:
        try:
            def _list():
                headers = {"Authorization": api_key, "Content-Type": "application/json"}
                query = '''query { issues(first: %d, orderBy: updatedAt) {
                    nodes { id identifier title state { name } url }
                }}''' % limit
                resp = requests.post("https://api.linear.app/graphql", headers=headers, json={"query": query}, timeout=15)
                data = resp.json()
                if "data" in data:
                    nodes = data["data"].get("issues", {}).get("nodes", [])
                    return {"ok": True, "issues": [{"id": n.get("identifier", ""), "title": n.get("title", ""), "state": n.get("state", {}).get("name", ""), "url": n.get("url", "")} for n in nodes]}
                return {"ok": False, "error": json.dumps(data.get("errors", []))[:200]}
            return await asyncio.to_thread(_list)
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}
