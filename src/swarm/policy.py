import os
from dataclasses import dataclass
from typing import Dict, Any


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str


def _prod_explicitly_allowed() -> bool:
    """Production-affecting capabilities are blocked by default.

    They are only permitted when an operator sets ALLOW_PROD_CAPABILITIES to a
    truthy value. This is an explicit, deliberate opt-in: it is NEVER inferred
    from REPLIT_DEPLOYMENT, so simply publishing the app does not unlock
    production actions.
    """
    return os.environ.get("ALLOW_PROD_CAPABILITIES", "").lower() in ("1", "true", "yes", "on")


class PolicyEngine:
    def __init__(self):
        self.allow_prod = _prod_explicitly_allowed()

    def evaluate(self, capability: str, params: Dict[str, Any]) -> PolicyDecision:
        if capability in ("read_secret_raw", "dump_vault", "export_all_secrets"):
            return PolicyDecision(False, "Raw secret export is forbidden.")

        env = params.get("env")
        if env == "prod" and not self.allow_prod:
            return PolicyDecision(False, "Production actions are blocked by policy.")

        if capability == "terraform_apply" and not params.get("has_plan"):
            return PolicyDecision(False, "terraform_apply requires a prior terraform_plan.")

        return PolicyDecision(True, "Allowed by policy.")
