"""env_1 loader for the browser capability.

Reads the ``[browser]`` selection and ``[browserbackends.*]`` definitions from
config/providers.toml (the same committed mapping layer the LLM providers use).
Contains no secret values — only the NAMES of the secrets each backend needs.
"""
from typing import Dict, List, Optional

from src.browser.base import BrowserBackendSpec
from src.providers.config import load_config


def _spec_from_entry(key: str, d: dict) -> BrowserBackendSpec:
    return BrowserBackendSpec(
        key=key,
        adapter=d.get("adapter", ""),
        cost=float(d.get("cost", 0) or 0),
        secrets=list(d.get("secrets", [])),
        settings=dict(d.get("settings", {}) or {}),
        label=d.get("label", ""),
        notes=d.get("notes", ""),
    )


def defined_backend_specs() -> Dict[str, BrowserBackendSpec]:
    """All backends defined under [browserbackends.*], keyed by backend key."""
    cfg = load_config()
    backends = cfg.get("browserbackends", {}) or {}
    return {k: _spec_from_entry(k, v) for k, v in backends.items()}


def browser_capability() -> Optional[str]:
    return (load_config().get("capabilities", {}) or {}).get("browser")


def browser_enabled_keys() -> List[str]:
    return list((load_config().get("browser", {}) or {}).get("enabled", []))


def browser_required_keys() -> List[str]:
    return list((load_config().get("browser", {}) or {}).get("required", []))


def ordered_backend_specs() -> List[BrowserBackendSpec]:
    """Enabled backend specs, cheapest cost first (ties keep declared order)."""
    defined = defined_backend_specs()
    enabled = [defined[k] for k in browser_enabled_keys() if k in defined]
    return sorted(enabled, key=lambda s: s.cost)
