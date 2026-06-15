import logging
import subprocess
import sys
from dataclasses import dataclass
from typing import Dict, Any, Optional

from src.swarm.policy import PolicyEngine, PolicyDecision
from src.swarm.integrations import Integrations, audit_capability
from src.swarm.capability_contracts import validate_request as _contract_validate

try:
    from infra.mydude.routing.jurisdiction import jurisdiction_hint as _jurisdiction_hint
except Exception:
    def _jurisdiction_hint(domain: str = "general", team: str = "default") -> dict:
        return {}

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Runtime capability registry — populated by governance-approved acquisitions.
# Maps capability name → {package, version, registry, installed_at}.
# Only populated via register_acquired_capability(); never mutated directly.
# ---------------------------------------------------------------------------
_acquired_capabilities: Dict[str, Dict[str, Any]] = {}


def register_acquired_capability(
    capability: str,
    package: str,
    version: str,
    registry: str,
) -> bool:
    """Register a governance-approved acquired capability in the runtime registry.

    Called by _apply_acquisition_enactment() after a governance proposal is
    enacted. Installs the approved package into the live runtime (pip), then
    records the capability → package binding so the broker can dispatch future
    requests instead of treating the capability as unimplemented.

    Fails loud via return value (never raises) — the caller should audit the
    outcome. Returns True on successful install+register, False on failure.
    """
    from datetime import datetime
    try:
        install_spec = f"{package}=={version}" if version else package
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", install_spec,
             "--quiet", "--no-input", "--disable-pip-version-check"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            logger.warning(
                "register_acquired_capability: pip install failed for %s: %s",
                install_spec, result.stderr[:300],
            )
            return False

        _acquired_capabilities[capability] = {
            "package": package,
            "version": version,
            "registry": registry,
            "installed_at": datetime.utcnow().isoformat(),
        }
        logger.info(
            "register_acquired_capability: capability '%s' registered via %s==%s (%s)",
            capability, package, version, registry,
        )
        return True
    except Exception as exc:
        logger.warning("register_acquired_capability failed: %s", exc)
        return False


# Capabilities whose denials we record to the audit trail (the governed,
# externally-reaching ones). Pure internal/stub capabilities are not logged.
_AUDITED_CAPABILITIES = {
    "browser_open", "browser_login", "browser_cancel",
    "ssh_run", "ssh_read_history", "ssh_fetch_code",
    "imap_read_receipts", "gmail_fetch_code",
    "bot_spawn", "fleet_provision_plan", "fleet_provision_approve",
    "calendly_book",
    "telephony_place_call", "telephony_receive_call", "telephony_turn",
    "voice_synthesize",
    "azure_cosmos_read", "azure_pg_select", "azure_deploy_status",
    "azure_aoai_complete", "azure_deploy_plan", "azure_deploy_apply",
    "memory_recall",
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
        # Step 1: Contract validation — BEFORE the policy gate.
        # Malformed or under-justified requests are rejected here with a clear
        # reason recorded in the capability audit log.
        # _contract_validate() audits ALL violations internally via _audit_violation()
        # regardless of category — no need for a separate conditional audit here.
        contract_violation = _contract_validate(capability, params)
        if contract_violation:
            # Surface contract violations in the Governance Center as sentinel
            # alerts, not just buried in logs / the capability audit trail.
            # Operators need to see malformed or under-justified capability
            # requests since they often indicate a misbehaving agent or role.
            try:
                from src.swarm.error_metrics import record_sentinel_event
                # Never use credential-bearing params as the alert target.
                target = (params.get("url") or params.get("login_url")
                          or params.get("command") or params.get("browser"))
                description = f"Capability '{capability}' rejected by contract: {contract_violation}"
                if target:
                    description += f" (target: {target})"
                record_sentinel_event(
                    alert_type="contract_violation",
                    severity="warning",
                    description=description,
                    recommended_action=(
                        "Review the requesting agent/role; ensure capability "
                        "requests are well-formed and carry required justification."
                    ),
                )
            except Exception:
                pass
            return BrokerResult(False, PolicyDecision(False, contract_violation), None)

        # Step 2: Jurisdiction routing hint injected before policy evaluation.
        # If PG_AGENTS_HOME_DSN is not set the hint is an empty dict (no-op).
        # The _jurisdiction key is stripped by policy.evaluate if it doesn't recognise it.
        domain = params.get("domain", "general")
        team = params.get("team", "default")
        hint = _jurisdiction_hint(domain=domain, team=team)
        if hint:
            params = {**params, **hint}

        # Step 3: Policy gate.
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

        if capability == "bot_spawn":
            out = await self.integrations.bot_spawn(params)
            return BrokerResult(True, decision, out)

        if capability == "fleet_provision_plan":
            out = await self.integrations.fleet_provision_plan(params)
            return BrokerResult(True, decision, out)

        if capability == "fleet_provision_approve":
            out = await self.integrations.fleet_provision_approve(params)
            return BrokerResult(True, decision, out)

        if capability == "calendly_book":
            out = await self.integrations.calendly_book(params)
            return BrokerResult(True, decision, out)

        if capability == "voice_synthesize":
            out = await self.integrations.voice_synthesize(params)
            return BrokerResult(True, decision, out)

        if capability == "telephony_place_call":
            out = await self.integrations.telephony_place_call(params)
            return BrokerResult(True, decision, out)

        if capability == "telephony_receive_call":
            out = await self.integrations.telephony_receive_call(params)
            return BrokerResult(True, decision, out)

        if capability == "telephony_turn":
            out = await self.integrations.telephony_turn(params)
            return BrokerResult(True, decision, out)

        if capability == "azure_cosmos_read":
            out = await self.integrations.azure_cosmos_read(params)
            return BrokerResult(True, decision, out)

        if capability == "azure_pg_select":
            out = await self.integrations.azure_pg_select(params)
            return BrokerResult(True, decision, out)

        if capability == "azure_deploy_status":
            out = await self.integrations.azure_deploy_status(params)
            return BrokerResult(True, decision, out)

        if capability == "azure_aoai_complete":
            out = await self.integrations.azure_aoai_complete(params)
            return BrokerResult(True, decision, out)

        if capability == "azure_deploy_plan":
            out = await self.integrations.azure_deploy_plan(params)
            return BrokerResult(True, decision, out)

        if capability == "azure_deploy_apply":
            out = await self.integrations.azure_deploy_apply(params)
            return BrokerResult(True, decision, out)

        if capability == "memory_recall":
            out = await self.integrations.memory_recall(params)
            return BrokerResult(True, decision, out)

        # Check runtime registry populated by governance-approved acquisitions.
        # If an acquired package has been installed and registered for this
        # capability, return a live (non-stub) result instead of unimplemented.
        if capability in _acquired_capabilities:
            reg = _acquired_capabilities[capability]
            return BrokerResult(
                True, decision,
                f"Capability '{capability}' dispatched via acquired package "
                f"{reg['package']}=={reg['version']} ({reg['registry']}). "
                f"Package is installed in the live runtime and available for import.",
            )

        # A genuinely-new (unimplemented) capability is being requested.
        # DevGuard's dedup check runs SYNCHRONOUSLY here so its verdict can
        # gate the acquisition trigger below. For the normal (non-acquisition)
        # path this adds negligible latency (DevGuard is a fast index lookup);
        # when acquisition is disabled the try/except overhead is imperceptible.
        # Returns truthy if an equivalent already exists in the codebase —
        # acquisition must be skipped in that case to avoid redundant jobs.
        _devguard_equivalent_found = False
        try:
            from agentledger.experimental.devguard.capability_guard import on_new_capability
            _devguard_result = on_new_capability(capability, params)
            _devguard_equivalent_found = bool(_devguard_result)
        except Exception:
            # DevGuard unavailable or unimplemented — conservative: assume no
            # equivalent (fall through to DB dedup + governance gate).
            _devguard_equivalent_found = False

        # Auto-Siphon Acquisition Loop (Phase 2 ASRE) — when the kill switch
        # ENABLE_AUTO_SIPHON_ACQUISITION=true is set, open an acquisition job
        # to autonomously find, verify, and govern a candidate package that
        # satisfies this deficit. Gated on the synchronous DevGuard dedup
        # verdict above: if an equivalent was already found in the codebase,
        # skip acquisition entirely. Otherwise fire-and-forget (daemon thread);
        # never blocks the broker response. When off, reverts to detect-and-alert only.
        if not _devguard_equivalent_found:
            try:
                from src.acquisition.orchestrator import trigger_acquisition
                trigger_acquisition(capability, params)
            except Exception:
                pass

        return BrokerResult(True, decision, f"Capability executed (stub): {capability} {params}")
