"""Boot-time browser-capability handshake.

Validates that every enabled backend references a defined [browserbackends.*]
block and a registered adapter, and that any backend listed in ``required`` has
its secrets present. A misconfiguration fails loud at boot. With the default
empty ``required`` list, the capability simply boots disabled until the operator
adds credentials — never a silent partial state.
"""
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


class BrowserHandshakeError(RuntimeError):
    """Raised at boot when the browser config is invalid or a required secret
    is missing. Propagated out of startup so the app refuses to serve traffic."""


def run_browser_handshake() -> Dict[str, object]:
    from src.browser.config import (
        browser_enabled_keys,
        browser_required_keys,
        defined_backend_specs,
    )
    from src.browser.registry import BACKEND_REGISTRY
    from src.providers.secrets import has_secret

    specs = defined_backend_specs()
    enabled = browser_enabled_keys()
    required = browser_required_keys()

    config_errors: List[str] = []
    for key in set(enabled) | set(required):
        spec = specs.get(key)
        if spec is None:
            config_errors.append(
                "backend '%s' is selected but has no [browserbackends.%s] definition"
                % (key, key)
            )
        elif spec.adapter not in BACKEND_REGISTRY:
            config_errors.append(
                "backend '%s' uses unknown adapter '%s' (registered: %s)"
                % (key, spec.adapter, ", ".join(sorted(BACKEND_REGISTRY)))
            )
    for key in required:
        if key not in enabled:
            config_errors.append(
                "backend '%s' is in browser.required but not in browser.enabled" % key
            )

    if config_errors:
        raise BrowserHandshakeError(
            "Browser config (env_1) is invalid:\n  - " + "\n  - ".join(config_errors)
        )

    missing: List[str] = []
    for key in required:
        for secret in specs[key].secrets:
            if not has_secret(secret):
                missing.append(
                    "backend '%s' requires secret '%s' — add it in the vault or "
                    "Replit Secrets" % (key, secret)
                )

    if missing:
        raise BrowserHandshakeError(
            "Browser handshake FAILED — missing required backend secrets:\n  - "
            + "\n  - ".join(missing)
        )

    configured = [
        k for k in enabled
        if k in specs and all(has_secret(s) for s in specs[k].secrets)
    ]
    summary = {"enabled": enabled, "required": required, "configured_at_boot": configured}
    logger.info(
        "Browser handshake OK — enabled=%s required=%s configured=%s",
        enabled, required, configured,
    )
    return summary
