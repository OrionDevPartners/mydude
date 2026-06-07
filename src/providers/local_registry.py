"""Local model registry reader.

Reads the per-install local model manifest (default
``~/.mydude/local/model_registry.yaml``) — the list of models that exist on
this machine for offline / sovereign inference (Ollama GGUF, Apple MLX).

The registry is OPTIONAL. When the file is absent (e.g. a cloud deployment with
no local sovereign stack installed) this degrades to an empty registry: the
local providers simply fall back to their ``default_model`` declared in env_1
(config/providers.toml). It is never a hard failure.

Used by:
  * the local LLM adapters (src/providers/adapters.py) to pick a sensible
    default local model per provider when none is pinned via env, and
  * the jurisdiction router (infra/mydude/routing/jurisdiction.py) to know which
    local models exist for the local_degraded fallback tier.
"""
import os
import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

DEFAULT_REGISTRY_PATH = "~/.mydude/local/model_registry.yaml"


def registry_path() -> Path:
    """Resolve the registry path (env override > default), expanding ``~``."""
    raw = os.environ.get("LOCAL_MODEL_REGISTRY_PATH") or DEFAULT_REGISTRY_PATH
    return Path(raw).expanduser()


def _extract_models(data) -> List[dict]:
    """Normalise the several shapes the registry file may take into a flat list.

    Accepts:
      * a bare list of model dicts
      * ``{"models": [...]}``
      * the sovereign_stack shape ``{"model_registry": {"format": [...]}}``
      * any mapping containing a list of dicts that look like model entries
    """
    if data is None:
        return []
    if isinstance(data, list):
        return [m for m in data if isinstance(m, dict) and m.get("model_id")]
    if isinstance(data, dict):
        if isinstance(data.get("models"), list):
            return _extract_models(data["models"])
        mr = data.get("model_registry")
        if isinstance(mr, dict) and isinstance(mr.get("format"), list):
            return _extract_models(mr["format"])
        # Best-effort: first value that is a list of model-like dicts.
        for v in data.values():
            if isinstance(v, list) and any(
                isinstance(m, dict) and m.get("model_id") for m in v
            ):
                return _extract_models(v)
    return []


def load_local_models() -> List[dict]:
    """Return the list of locally-registered models, or [] if unavailable.

    Never raises — a missing/malformed registry yields an empty list with a log
    line, so startup and routing degrade gracefully.
    """
    p = registry_path()
    if not p.exists():
        logger.info(
            "Local model registry not found at %s; no local models registered.", p
        )
        return []
    try:
        import yaml  # type: ignore
    except Exception:
        logger.warning(
            "pyyaml not available; cannot read local model registry at %s.", p
        )
        return []
    try:
        with open(p, "r") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        logger.warning("Failed to parse local model registry %s: %s", p, e)
        return []
    models = _extract_models(data)
    logger.info("Local model registry: %d model(s) loaded from %s", len(models), p)
    return models


def local_models_for_provider(provider: str) -> List[dict]:
    """All registered models whose ``provider`` matches (e.g. 'ollama', 'mlx')."""
    return [m for m in load_local_models() if m.get("provider") == provider]


def default_model_for_provider(provider: str, fallback: str = "") -> Optional[str]:
    """First registered model id for ``provider``, else ``fallback``.

    Lets a local adapter prefer an actually-installed model over its static
    config default, without requiring an env override.
    """
    models = local_models_for_provider(provider)
    if models:
        return models[0].get("model_id") or fallback
    return fallback
