from dataclasses import dataclass
from typing import Dict, Any, Optional

from src.swarm.policy import PolicyEngine, PolicyDecision
from src.swarm.integrations import Integrations


@dataclass
class BrokerResult:
    ok: bool
    decision: PolicyDecision
    output: Optional[str] = None


class CapabilityBroker:
    def __init__(self, policy: PolicyEngine, integrations: Integrations):
        self.policy = policy
        self.integrations = integrations

    async def request(self, capability: str, params: Dict[str, Any]) -> BrokerResult:
        decision = self.policy.evaluate(capability, params)
        if not decision.allowed:
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

        return BrokerResult(True, decision, f"Capability executed (stub): {capability} {params}")
