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

# Curated set of request domains operators can route to. Each domain can be
# pinned to a stricter exec_locus / model team via policy.model_team_policy in
# agents_home; "general" is the default (cheapest cloud). The list is the single
# source of truth shared by the web UI and the run endpoints — extend it here
# when a new governed domain is onboarded.
JURISDICTION_DOMAINS = [
    "general",
    "legal",
    "finance",
    "medical",
    "engineering",
    "marketing",
    "customer_service",
    "hr",
]


def normalize_domain(value: Optional[str]) -> str:
    """Coerce arbitrary user input into a safe domain slug.

    Lower-cased, whitespace/spaces collapsed to underscores, bounded length.
    Falls back to "general" when empty. The value is intentionally not
    constrained to JURISDICTION_DOMAINS — agents_home policy is data-driven and
    operators may configure additional domains beyond the curated UI list.
    """
    if not value:
        return "general"
    slug = "_".join(str(value).strip().lower().split())
    return slug[:100] or "general"


def normalize_team(value: Optional[str]) -> str:
    """Coerce arbitrary user input into a safe team slug (defaults to "default")."""
    if not value:
        return "default"
    slug = "_".join(str(value).strip().lower().split())
    return slug[:100] or "default"

# Short TTL cache for the cloud_shift lookup. The swarm consults cloud_shift on
# every provider fanout (several times per task); without this, an
# agents_home-backed deployment would open a synchronous DB connection in the
# event loop on each call. A few seconds of staleness is acceptable for a kill
# switch and keeps the hot path cheap.
_CLOUD_SHIFT_TTL = 5.0
_cloud_shift_cache: Optional[bool] = None
_cloud_shift_ts: float = 0.0

# app_settings key holding the operator override persisted from the dashboard
# when no agents_home DSN is configured. Value is the literal "true"/"false".
CLOUD_SHIFT_OVERRIDE_KEY = "cloud_shift_override"


def _agents_home_dsn() -> str:
    return os.environ.get("PG_AGENTS_HOME_DSN", "")


def _cloud_shift_kill_switch():
    """Import and return a CloudShiftKillSwitch bound to the agents_home DSN.

    Raises if the infra routing module cannot be imported so callers can decide
    how to degrade.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "infra" / "mydude" / "routing"))
    from jurisdiction import CloudShiftKillSwitch
    return CloudShiftKillSwitch(dsn=_agents_home_dsn())


def _read_override() -> Optional[bool]:
    """Return the persisted dashboard override (True/False) or None if unset."""
    try:
        from src.web.settings_store import get_setting
        raw = get_setting(CLOUD_SHIFT_OVERRIDE_KEY)
    except Exception as e:
        logger.debug("cloud_shift override read failed (%s); ignoring.", e)
        return None
    if raw is None:
        return None
    val = str(raw).strip().lower()
    if val in ("false", "0", "no", "off"):
        return False
    if val in ("true", "1", "yes", "on"):
        return True
    return None


def _resolve_cloud_shift() -> bool:
    # 1. agents_home DB is the authoritative shared store when a DSN is set.
    #    set_cloud_shift() writes here via CloudShiftKillSwitch.set_enabled(),
    #    so a dashboard toggle is reflected on the next read.
    if _agents_home_dsn():
        try:
            return _cloud_shift_kill_switch().is_enabled()
        except Exception as e:
            logger.debug("agents_home cloud_shift query failed (%s); falling back.", e)

    # 2. Operator override persisted from the dashboard (no-DSN path). This is a
    #    deliberate runtime kill-switch action, so it wins over the static env
    #    default below.
    override = _read_override()
    if override is not None:
        return override

    # 3. Static deploy-time default.
    env_override = os.environ.get("CLOUD_SHIFT_ENABLED", "").lower()
    if env_override in ("false", "0", "no", "off"):
        logger.info("cloud_shift disabled by CLOUD_SHIFT_ENABLED env var.")
        return False
    if env_override in ("true", "1", "yes", "on"):
        return True

    # 4. Default: cloud egress permitted.
    return True


def get_cloud_shift() -> bool:
    """Return True if cloud egress is permitted (cached for _CLOUD_SHIFT_TTL).

    Resolution order: agents_home.routing.cloud_shift (when a DSN is set) →
    the persisted dashboard override in app_settings → the CLOUD_SHIFT_ENABLED
    env var → default (true).
    """
    global _cloud_shift_cache, _cloud_shift_ts
    now = time.monotonic()
    if _cloud_shift_cache is None or (now - _cloud_shift_ts) > _CLOUD_SHIFT_TTL:
        _cloud_shift_cache = _resolve_cloud_shift()
        _cloud_shift_ts = now
    return _cloud_shift_cache


def invalidate_cloud_shift_cache() -> None:
    """Drop the cached cloud_shift value so the next read re-resolves it."""
    global _cloud_shift_cache, _cloud_shift_ts
    _cloud_shift_cache = None
    _cloud_shift_ts = 0.0


def set_cloud_shift(enabled: bool, reason: str = "", updated_by: str = "operator") -> dict:
    """Flip the cloud_shift kill switch and persist it so the runtime reads it.

    When a PG_AGENTS_HOME_DSN is configured the change is written to the shared
    agents_home store via CloudShiftKillSwitch.set_enabled(); otherwise it is
    persisted as a dashboard override in app_settings. The in-process cache is
    invalidated so the new state takes effect (and is reported back) immediately.

    Returns a dict: {"requested", "effective", "source"}. ``effective`` is the
    re-resolved live value — if it differs from ``requested`` an env-level
    override is in force and the caller should surface that to the operator.
    """
    if _agents_home_dsn():
        _cloud_shift_kill_switch().set_enabled(enabled, reason=reason, updated_by=updated_by)
        source = "agents_home"
    else:
        from src.web.settings_store import set_setting
        set_setting(CLOUD_SHIFT_OVERRIDE_KEY, "true" if enabled else "false")
        source = "app_settings"

    invalidate_cloud_shift_cache()
    effective = get_cloud_shift()
    logger.info(
        "cloud_shift set to %s by %s via %s (effective=%s): %s",
        enabled, updated_by, source, effective, reason or "(no reason)",
    )
    return {"requested": enabled, "effective": effective, "source": source}


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


def get_exec_locus_pin() -> Optional[str]:
    """Return the EXEC_LOCUS_PIN env override (e.g. 'local'), or None when unset.

    A blank value or the literal 'any' both mean "no pin" (all loci eligible).
    The pin hard-restricts provider selection to a single exec_locus, which is
    how an operator forces local-only (sovereign) execution independent of the
    cloud_shift kill switch.
    """
    val = (os.environ.get("EXEC_LOCUS_PIN", "") or "").strip()
    if not val or val.lower() == "any":
        return None
    return val


def provider_passes_jurisdiction(
    locus: str,
    exec_locus_pin: Optional[str],
    cloud_shift_active: bool,
) -> bool:
    """Single source of truth for whether one provider survives the jurisdiction.

    ``locus`` is the provider's exec_locus (from config/providers.toml). The two
    gates, in order:
      1. cloud_shift kill switch — when cloud egress is disabled, only local
         (exec_locus=local) providers survive.
      2. exec_locus pin — when pinned to a concrete locus, only providers whose
         exec_locus matches survive ('local' matches exec_locus=local providers).

    Both the live swarm (MultiProviderLLM) and permitted_provider_keys() route
    through this predicate so the tested behaviour is exactly the served one.
    """
    is_local = locus == "local"
    # Kill switch: no cloud egress -> only local providers survive.
    if not cloud_shift_active and not is_local:
        return False
    # exec_locus pin: when pinned, only matching providers survive.
    if exec_locus_pin not in ("any", "", None):
        if exec_locus_pin == "local":
            return is_local
        return locus == exec_locus_pin
    return True


def permitted_provider_keys(
    provider_keys: Optional[list] = None,
    exec_locus_pin: Optional[str] = None,
    cloud_shift_active: Optional[bool] = None,
) -> list:
    """Return the subset of provider keys permitted under the current jurisdiction.

    With no arguments this reflects the live environment:
      * ``provider_keys`` defaults to the LLM providers enabled in env_1
        (config/providers.toml, in declared order).
      * ``exec_locus_pin`` defaults to the EXEC_LOCUS_PIN env override.
      * ``cloud_shift_active`` defaults to get_cloud_shift() (CLOUD_SHIFT_ENABLED
        env / agents_home).

    The result is the local_degraded safety net: when cloud_shift is off or the
    locus is pinned to 'local', only the local providers (Ollama/MLX) remain.
    """
    if provider_keys is None:
        try:
            from src.providers.config import llm_enabled_keys
            provider_keys = llm_enabled_keys()
        except Exception as e:
            logger.debug("permitted_provider_keys: could not load enabled keys (%s).", e)
            provider_keys = []
    if exec_locus_pin is None:
        exec_locus_pin = get_exec_locus_pin()
    if cloud_shift_active is None:
        cloud_shift_active = get_cloud_shift()
    return [
        k
        for k in provider_keys
        if provider_passes_jurisdiction(get_exec_locus(k), exec_locus_pin, cloud_shift_active)
    ]


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
