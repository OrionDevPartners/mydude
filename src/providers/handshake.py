"""Boot-time provider handshake.

Validates env_1 integrity and that every secret required by the selected
providers is present in env_2 BEFORE the app serves traffic. A missing required
secret is a hard failure at boot with a clear, specific message — never a silent
fallback or a deferred crash.
"""
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


class ProviderHandshakeError(RuntimeError):
    """Raised at boot when provider config is invalid or a required secret is
    missing. Propagated out of startup so the app refuses to serve traffic."""


def run_handshake() -> Dict[str, object]:
    """Validate provider config + required secrets. Raise on any problem.

    Returns a summary dict on success.
    """
    from src.providers.config import (
        load_config,
        defined_provider_specs,
        llm_enabled_keys,
        llm_required_keys,
    )
    from src.providers.registry import ADAPTER_REGISTRY
    from src.providers.secrets import has_secret

    load_config()  # raises ProviderConfigError if missing/malformed
    specs = defined_provider_specs()
    enabled = llm_enabled_keys()
    required = llm_required_keys()

    # --- config integrity ---------------------------------------------------
    config_errors: List[str] = []
    for key in set(enabled) | set(required):
        spec = specs.get(key)
        if spec is None:
            config_errors.append(
                "provider '%s' is selected but has no [providers.%s] definition"
                % (key, key)
            )
        elif spec.adapter not in ADAPTER_REGISTRY:
            config_errors.append(
                "provider '%s' uses unknown adapter '%s' (registered: %s)"
                % (key, spec.adapter, ", ".join(sorted(ADAPTER_REGISTRY)))
            )
    for key in required:
        if key not in enabled:
            config_errors.append(
                "provider '%s' is in llm.required but not in llm.enabled" % key
            )

    if config_errors:
        raise ProviderHandshakeError(
            "Provider config (env_1) is invalid:\n  - "
            + "\n  - ".join(config_errors)
        )

    # --- required secret validation (env_2) ---------------------------------
    missing: List[str] = []
    for key in required:
        for secret in specs[key].secrets:
            if not has_secret(secret):
                missing.append(
                    "provider '%s' requires secret '%s' — set it in Replit "
                    "Secrets or add the key in the vault" % (key, secret)
                )

    if missing:
        raise ProviderHandshakeError(
            "Boot handshake FAILED — missing required provider secrets:\n  - "
            + "\n  - ".join(missing)
        )

    # --- summary ------------------------------------------------------------
    configured = [
        k for k in enabled
        if k in specs and all(has_secret(s) for s in specs[k].secrets)
    ]
    summary = {
        "capability_llm": (load_config().get("capabilities", {}) or {}).get("llm"),
        "enabled": enabled,
        "required": required,
        "configured_at_boot": configured,
    }
    logger.info(
        "Provider handshake OK — enabled=%s required=%s configured=%s",
        enabled, required, configured,
    )
    return summary
