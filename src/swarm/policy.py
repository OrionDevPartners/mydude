from dataclasses import dataclass
from typing import Dict, Any


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str


class PolicyEngine:
    def __init__(self):
        self.allow_prod = False

    def evaluate(self, capability: str, params: Dict[str, Any]) -> PolicyDecision:
        if capability in ("read_secret_raw", "dump_vault", "export_all_secrets"):
            return PolicyDecision(False, "Raw secret export is forbidden.")

        env = params.get("env")
        if env == "prod" and not self.allow_prod:
            return PolicyDecision(False, "Production actions are blocked by policy.")

        if capability == "terraform_apply" and not params.get("has_plan"):
            return PolicyDecision(False, "terraform_apply requires a prior terraform_plan.")

        return PolicyDecision(True, "Allowed by policy.")
