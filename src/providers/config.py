"""env_1 loader — reads config/providers.toml (the committed provider mapping
layer) and exposes provider specs / capability selections to the rest of the
app. Contains no secret values.
"""
import os
import tomllib
import functools
from pathlib import Path
from typing import Dict, List, Optional

from src.providers.base import ProviderSpec

DEFAULT_CONFIG_PATH = "config/providers.toml"


class ProviderConfigError(RuntimeError):
    """Raised when env_1 is missing or malformed."""


def _config_path() -> str:
    return os.environ.get("PROVIDER_CONFIG_PATH", DEFAULT_CONFIG_PATH)


@functools.lru_cache(maxsize=8)
def load_config(path: Optional[str] = None) -> dict:
    p = Path(path or _config_path())
    if not p.exists():
        raise ProviderConfigError(
            "Provider config (env_1) not found at '%s'. This file maps "
            "capabilities to providers and must be committed." % p
        )
    try:
        with open(p, "rb") as f:
            return tomllib.load(f)
    except Exception as e:
        raise ProviderConfigError("Failed to parse provider config '%s': %s" % (p, e))


def reload_config() -> None:
    load_config.cache_clear()


def _spec_from_entry(key: str, d: dict) -> ProviderSpec:
    return ProviderSpec(
        key=key,
        adapter=d.get("adapter", ""),
        secrets=list(d.get("secrets", [])),
        model_env=d.get("model_env", ""),
        default_model=d.get("default_model", ""),
        concurrency_env=d.get("concurrency_env", ""),
        default_concurrency=int(d.get("default_concurrency", 2)),
        role_hint=d.get("role_hint", ""),
        model_patterns=list(d.get("model_patterns", [])),
        base_url_env=d.get("base_url_env", ""),
        default_base_url=d.get("default_base_url", ""),
        alias_env=d.get("alias_env", ""),
    )


def defined_provider_specs() -> Dict[str, ProviderSpec]:
    """All providers defined under [providers.*], keyed by provider key."""
    cfg = load_config()
    providers = cfg.get("providers", {}) or {}
    return {k: _spec_from_entry(k, v) for k, v in providers.items()}


def capability(name: str) -> Optional[str]:
    return (load_config().get("capabilities", {}) or {}).get(name)


def llm_enabled_keys() -> List[str]:
    return list((load_config().get("llm", {}) or {}).get("enabled", []))


def llm_required_keys() -> List[str]:
    return list((load_config().get("llm", {}) or {}).get("required", []))


def llm_provider_specs() -> List[ProviderSpec]:
    """Specs for the LLM providers enabled in env_1, in declared order."""
    defined = defined_provider_specs()
    return [defined[k] for k in llm_enabled_keys() if k in defined]


def provider_env_map() -> Dict[str, str]:
    """Map provider key -> its primary secret env var name (from env_1).

    Lets call sites (vault, service catalog) derive secret names from the single
    committed source instead of hardcoding ``OPENAI_API_KEY`` etc.
    """
    return {
        k: (s.secrets[0] if s.secrets else "")
        for k, s in defined_provider_specs().items()
        if s.secrets
    }
