"""Unified boot-time capability handshake.

Validates config integrity and secret presence for ALL capability categories
before the app serves traffic. Replaces the two separate per-domain handshakes
(LLM + browser) with a single governed check that covers every category.

Fail-loud contract: any config error or missing required secret raises
``CapabilityHandshakeError`` with a specific, actionable message. The app
startup raises out and refuses to serve traffic rather than booting into a
silently degraded state.

Behavior preservation guarantee: the LLM and browser categories are validated
through their original handshake logic (re-used internally), so their exact
validation rules and error messages are preserved while also being covered by
the unified run.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class CapabilityHandshakeError(RuntimeError):
    """Raised at boot when any capability config is invalid or a required secret
    is missing. Propagated out of startup so the app refuses to serve traffic."""


def run_unified_handshake() -> Dict[str, Any]:
    """Validate ALL capability categories. Raise on any problem.

    Returns a summary dict on success::

        {
          "categories": {
            "llm": {"enabled": [...], "required": [...], "configured": [...]},
            "browser": {...},
            ...
          },
          "errors": [],
          "warnings": [],
        }
    """
    from src.capabilities.config import ALL_CATEGORIES, category_enabled_keys, category_required_keys, defined_specs_for
    from src.capabilities.registry import CAPABILITY_REGISTRY, registered_adapters_for
    from src.providers.secrets import has_secret

    all_errors: List[str] = []
    all_warnings: List[str] = []
    category_summaries: Dict[str, Any] = {}

    # -----------------------------------------------------------------------
    # LLM — delegate to the original handshake for behavior preservation
    # -----------------------------------------------------------------------
    try:
        from src.providers.handshake import run_handshake
        llm_summary = run_handshake()
        category_summaries["llm"] = llm_summary
    except Exception as exc:
        all_errors.append("[llm] %s" % exc)

    # -----------------------------------------------------------------------
    # Browser — delegate to the original handshake for behavior preservation
    # -----------------------------------------------------------------------
    try:
        from src.browser.handshake import run_browser_handshake
        browser_summary = run_browser_handshake()
        category_summaries["browser"] = browser_summary
    except Exception as exc:
        all_errors.append("[browser] %s" % exc)

    # -----------------------------------------------------------------------
    # All other categories — generic validation loop
    # -----------------------------------------------------------------------
    for category in ALL_CATEGORIES:
        if category in ("llm", "browser"):
            continue  # already validated above

        enabled = category_enabled_keys(category)
        required = category_required_keys(category)
        defined = defined_specs_for(category)
        registered = registered_adapters_for(category)

        config_errors: List[str] = []

        # Config integrity: every enabled/required key must have a definition
        # and a registered adapter.
        for key in set(enabled) | set(required):
            spec = defined.get(key)
            if spec is None:
                config_errors.append(
                    "provider '%s' is selected but has no [%sbackends.%s] "
                    "definition in config/providers.toml" % (key, category, key)
                )
                continue
            if spec.adapter not in registered:
                config_errors.append(
                    "provider '%s' uses adapter '%s' which is not registered for "
                    "category '%s' (registered: %s)" % (
                        key, spec.adapter, category,
                        ", ".join(registered) or "none",
                    )
                )

        for key in required:
            if key not in enabled:
                config_errors.append(
                    "provider '%s' is in [%s].required but not in [%s].enabled"
                    % (key, category, category)
                )

        if config_errors:
            for err in config_errors:
                all_errors.append("[%s] %s" % (category, err))
            continue

        # Secret validation: every required provider must have its secrets.
        missing_secrets: List[str] = []
        for key in required:
            spec = defined.get(key)
            if spec is None:
                continue
            for secret in spec.secrets:
                if not has_secret(secret):
                    missing_secrets.append(
                        "provider '%s' requires secret '%s' — set it in "
                        "Replit Secrets or add the key in the vault" % (key, secret)
                    )

        if missing_secrets:
            for m in missing_secrets:
                all_errors.append("[%s] %s" % (category, m))
            continue

        # Summary for this category.
        configured = [
            k for k in enabled
            if k in defined and all(has_secret(s) for s in defined[k].secrets)
        ]
        category_summaries[category] = {
            "enabled": enabled,
            "required": required,
            "configured_at_boot": configured,
        }
        logger.info(
            "Capability handshake OK [%s] — enabled=%s required=%s configured=%s",
            category, enabled, required, configured,
        )

    # -----------------------------------------------------------------------
    # Lint: warn about categories with no enabled providers
    # -----------------------------------------------------------------------
    for category in ALL_CATEGORIES:
        if category in category_summaries:
            continue
        if category in ("llm", "browser"):
            continue
        enabled = category_enabled_keys(category)
        if not enabled:
            all_warnings.append(
                "[%s] no providers enabled — category will be unavailable. "
                "Add a [%s].enabled list to config/providers.toml to activate it."
                % (category, category)
            )
        category_summaries[category] = {
            "enabled": enabled,
            "required": category_required_keys(category),
            "configured_at_boot": [],
        }

    if all_errors:
        raise CapabilityHandshakeError(
            "Capability handshake FAILED:\n  - " + "\n  - ".join(all_errors)
        )

    for w in all_warnings:
        logger.warning("Capability handshake warning: %s", w)

    logger.info(
        "Unified capability handshake OK — %d categories validated, %d warning(s)",
        len(category_summaries), len(all_warnings),
    )
    return {
        "categories": category_summaries,
        "errors": all_errors,
        "warnings": all_warnings,
    }
