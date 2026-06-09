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
import time
from typing import Optional

logger = logging.getLogger("mydude.swarm.jurisdiction")

# Short TTL cache for the cloud_shift lookup. The swarm consults cloud_shift on
# every provider fanout (several times per task); without this, an
# agents_home-backed deployment would open a synchronous DB connection in the
# event loop on each call. A few seconds of staleness is acceptable for a kill
# switch and keeps the hot path cheap.
_CLOUD_SHIFT_TTL = 5.0
_cloud_shift_cache: Optional[bool] = None
_cloud_shift_ts: float = 0.0


def _resolve_cloud_shift() -> bool:
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


def get_cloud_shift() -> bool:
    """Return True if cloud egress is permitted (cached for _CLOUD_SHIFT_TTL).

    Reads from agents_home.routing.cloud_shift when configured; falls back to
    the CLOUD_SHIFT_ENABLED env var (default: true).
    """
    global _cloud_shift_cache, _cloud_shift_ts
    now = time.monotonic()
    if _cloud_shift_cache is None or (now - _cloud_shift_ts) > _CLOUD_SHIFT_TTL:
        _cloud_shift_cache = _resolve_cloud_shift()
        _cloud_shift_ts = now
    return _cloud_shift_cache


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


def _probe_local_endpoint(base_url: str, timeout: float = 0.5) -> bool:
    """Thin wrapper around the adapter TCP probe for use outside adapter instances."""
    import socket
    from urllib.parse import urlparse
    try:
        parsed = urlparse(base_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _local_endpoint_status(spec) -> dict:
    """Return endpoint + live reachability info for a local (exec_locus=local) provider.

    Reads the configured base URL from env (same resolution as the adapter) and
    probes it with a short TCP connect, respecting the provider-specific timeout env
    var (e.g. OLLAMA_PROBE_TIMEOUT) and the shared LOCAL_PROBE_TIMEOUT fallback.
    The status keys are:
      endpoint   — the resolved base URL string
      server_up  — True/False/None (None if probe was skipped)
    """
    import os
    from src.providers.secrets import get_env

    base_url = get_env(spec.base_url_env, spec.default_base_url) or ""

    if not base_url:
        return {"endpoint": "", "server_up": None}

    # Resolve probe timeout: provider-specific, then shared, then 0.5 s default.
    provider_timeout_env = f"{spec.key.upper()}_PROBE_TIMEOUT"
    timeout = 0.5
    for env_name in (provider_timeout_env, "LOCAL_PROBE_TIMEOUT"):
        val = os.environ.get(env_name, "").strip()
        if val:
            try:
                timeout = float(val)
                break
            except ValueError:
                pass

    server_up = _probe_local_endpoint(base_url, timeout=timeout)
    return {"endpoint": base_url, "server_up": server_up}


def provider_exec_locus_distribution() -> list:
    """Return per-provider exec_locus + availability for the governance dashboard.

    Each entry: {provider, exec_locus, available, routable, endpoint, server_up}.
    ``routable`` is True when the provider has its secret AND survives the current
    cloud_shift state (cloud providers are dropped when cloud_shift is disabled).

    For local (exec_locus=local) providers ``endpoint`` is the configured base URL
    (localhost or a Cloudflare Mesh IP) and ``server_up`` is the live TCP probe
    result, so operators can see the Mesh link status at a glance.
    """
    out: list = []
    try:
        from src.providers.config import llm_provider_specs
        from src.providers.secrets import has_secret

        cloud_shift = get_cloud_shift()
        for spec in llm_provider_specs():
            locus = get_exec_locus(spec.key)
            is_local = locus == "local"
            available = bool(spec.secrets) and all(has_secret(s) for s in spec.secrets)
            if is_local:
                # Local providers have no secrets; availability is purely server-up.
                available = True
            routable = available and (cloud_shift or is_local)
            entry: dict = {
                "provider": spec.key,
                "exec_locus": locus,
                "available": available,
                "routable": routable,
                "endpoint": None,
                "server_up": None,
            }
            if is_local:
                try:
                    entry.update(_local_endpoint_status(spec))
                except Exception as e:
                    logger.debug("local endpoint probe failed for %s: %s", spec.key, e)
            out.append(entry)
    except Exception as e:
        logger.debug("provider_exec_locus_distribution failed: %s", e)
    return out


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
        # Kill switch: when cloud egress is disabled we pin to local execution
        # and record the local_degraded fallback tier (4) rather than preferred (1).
        "exec_locus": "local" if not cloud_shift_active else "in_azure",
        "fallback_tier": 4 if not cloud_shift_active else 1,
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
