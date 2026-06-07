"""MyDude jurisdiction routing — thin integration shim.

Connects the existing src/swarm/broker.py + policy.py layer to the
agents_home jurisdiction routing ladder defined in
infra/mydude/routing/jurisdiction.py.

Call jurisdiction_hint() to get routing metadata that the WaveOrchestrator
and MultiProviderLLM can use to:
  - pick the right providers for the exec_locus of a domain
  - respect the cloud_shift kill switch
  - record the fallback tier that was used

This module never imports from infra/ at boot — it degrades gracefully
when agents_home is unreachable (local-only mode).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("mydude.swarm.jurisdiction")


def get_cloud_shift() -> bool:
    """Return True if cloud egress is permitted.

    Reads from agents_home.routing.cloud_shift when configured; falls back to
    the CLOUD_SHIFT_ENABLED env var (default: true).
    """
    env_override = os.environ.get("CLOUD_SHIFT_ENABLED", "").lower()
    if env_override in ("false", "0", "no", "off"):
        logger.info("cloud_shift disabled by CLOUD_SHIFT_ENABLED env var.")
        return False
    if env_override in ("true", "1", "yes", "on"):
        return True

    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "infra" / "mydude" / "routing"))
        from jurisdiction import CloudShiftKillSwitch
        return CloudShiftKillSwitch().is_enabled()
    except Exception as e:
        logger.debug("agents_home cloud_shift query failed (%s); defaulting to enabled.", e)
        return True


def get_exec_locus(provider_key: str) -> str:
    """Return the exec_locus for a provider key from config/providers.toml.

    Falls back to 'in_azure' if not declared.
    """
    try:
        from src.providers.config import load_config
        cfg = load_config()
        providers = cfg.get("providers", {}) or {}
        prov = providers.get(provider_key, {})
        return prov.get("exec_locus", "in_azure")
    except Exception:
        return "in_azure"


def filter_providers_by_exec_locus(provider_keys: list, domain_exec_locus_pin: str = "any") -> list:
    """Filter a list of provider keys to those whose exec_locus matches the domain pin.

    If domain_exec_locus_pin is 'any', all providers pass.
    """
    if domain_exec_locus_pin == "any":
        return list(provider_keys)
    return [k for k in provider_keys if get_exec_locus(k) == domain_exec_locus_pin]


def jurisdiction_metadata(domain: str = "general", team: str = "default") -> dict:
    """Return jurisdiction routing metadata for a request.

    Used by the WaveOrchestrator to annotate task runs with routing decisions.
    Degrades gracefully when agents_home is unavailable.
    """
    cloud_shift_active = get_cloud_shift()
    metadata: dict = {
        "domain": domain,
        "team": team,
        "cloud_shift_active": cloud_shift_active,
        "exec_locus": "local" if not cloud_shift_active else "in_azure",
        "fallback_tier": 1,
        "jurisdiction_source": "env_fallback",
    }

    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "infra" / "mydude" / "routing"))
        from jurisdiction import JurisdictionRouter
        router = JurisdictionRouter()
        decision = router.decide(domain=domain, team=team)
        metadata.update({
            "exec_locus": decision.exec_locus.value,
            "fallback_tier": decision.fallback_tier.value,
            "resolved_provider": decision.resolved_provider,
            "resolved_model": decision.resolved_model,
            "cloud_shift_active": decision.cloud_shift_active,
            "local_only": decision.local_only,
            "outcome": decision.outcome.value,
            "jurisdiction_source": "agents_home",
        })
    except Exception as e:
        logger.debug("Jurisdiction router unavailable (%s); using env fallback.", e)

    return metadata
