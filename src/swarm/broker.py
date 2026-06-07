from dataclasses import dataclass
from typing import Dict, Any, Optional

from src.swarm.policy import PolicyEngine, PolicyDecision
from src.swarm.integrations import Integrations, audit_capability

try:
    from infra.mydude.routing.jurisdiction import jurisdiction_hint as _jurisdiction_hint
except Exception:
    def _jurisdiction_hint(domain: str = "general", team: str = "default") -> dict:
        return {}

# Capabilities whose denials we record to the audit trail (the governed,
# externally-reaching ones). Pure internal/stub capabilities are not logged.
_AUDITED_CAPABILITIES = {
    "browser_open", "browser_login", "browser_cancel",
    "ssh_run", "ssh_read_history", "ssh_fetch_code",
    "imap_read_receipts", "gmail_fetch_code",
}


@dataclass
class BrokerResult:
    ok: bool
    decision: PolicyDecision
    output: Optional[str] = None
    screenshot_b64: Optional[str] = None


class CapabilityBroker:
    def __init__(self, policy: PolicyEngine, integrations: Integrations):
        self.policy = policy
        self.integrations = integrations

    async def request(self, capability: str, params: Dict[str, Any]) -> BrokerResult:
        # Inject jurisdiction routing hint (_jurisdiction key) before policy evaluation.
        # If PG_AGENTS_HOME_DSN is not set the hint is an empty dict (no-op).
        # The _jurisdiction key is stripped by policy.evaluate if it doesn't recognise it.
        domain = params.get("domain", "general")
        team = params.get("team", "default")
        hint = _jurisdiction_hint(domain=domain, team=team)
        if hint:
            params = {**params, **hint}

        decision = self.policy.evaluate(capability, params)
        if not decision.allowed:
            # Record blocked attempts too, so the audit log captures the full
            # governance picture — not just successful executions.
            if capability in _AUDITED_CAPABILITIES:
                # Never use credential-bearing params as the audit target.
                target = (params.get("url") or params.get("login_url")
                          or params.get("command") or params.get("browser"))
                audit_capability(
                    capability, target=target, status="blocked",
                    detail=decision.reason, source=params.get("source"),
                )
            return BrokerResult(False, decision, None)

        if capability == "git_status":
            out = await self.integrations.git_status(params)
            return BrokerResult(True, decision, out)

        if capability == "terraform_plan":
            out = await self.integrations.terraform_plan(params)
            return BrokerResult(True, decision, out)

        if capability == "terraform_apply":
            out = await self.integrations.terraform_apply(params)
            return BrokerResult(True, decision, out)

        if capability == "asana_query":
            out = await self.integrations.asana_query(params)
            return BrokerResult(True, decision, out)

        if capability == "op_read_scoped":
            out = await self.integrations.op_read_scoped(params)
            return BrokerResult(True, decision, out)

        if capability == "browser_open":
            out = await self.integrations.browser_open(params)
            return BrokerResult(
                True, decision, out,
                screenshot_b64=getattr(self.integrations, "last_browser_screenshot", None),
            )

        if capability == "browser_login":
            out = await self.integrations.browser_login(params)
            return BrokerResult(
                True, decision, out,
                screenshot_b64=getattr(self.integrations, "last_browser_screenshot", None),
            )

        if capability == "browser_cancel":
            out = await self.integrations.browser_cancel(params)
            return BrokerResult(
                True, decision, out,
                screenshot_b64=getattr(self.integrations, "last_browser_screenshot", None),
            )

        if capability == "ssh_run":
            out = await self.integrations.ssh_run(params)
            return BrokerResult(True, decision, out)

        if capability == "ssh_read_history":
            out = await self.integrations.ssh_read_history(params)
            return BrokerResult(True, decision, out)

        if capability == "ssh_fetch_code":
            out = await self.integrations.ssh_fetch_code(params)
            return BrokerResult(True, decision, out)

        if capability == "imap_read_receipts":
            out = await self.integrations.imap_read_receipts(params)
            return BrokerResult(True, decision, out)

        if capability == "gmail_fetch_code":
            out = await self.integrations.gmail_fetch_code(params)
            return BrokerResult(True, decision, out)

        return BrokerResult(True, decision, f"Capability executed (stub): {capability} {params}")
