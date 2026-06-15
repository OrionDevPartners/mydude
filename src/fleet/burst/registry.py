"""Burst backend registry — resolves backend adapters from config/providers.toml.

Resolution order mirrors every other MyDude capability:
  1. Load [burst_compute] from config/providers.toml (env_1 — committed to git).
  2. Filter to enabled, in declared order.
  3. Return configured (is_configured() == True) backends first, then the
     rest so the caller can decide to raise or skip.

Adding a new backend:
  1. Write a one-file adapter in src/fleet/burst/backends/<name>_adapter.py
     that subclasses BurstBackend.
  2. Register it in the ADAPTERS map below.
  3. Add a [burstbackends.<name>] block in config/providers.toml.
  No other file needs to change.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Type

from src.fleet.burst.interface import BurstBackend

logger = logging.getLogger(__name__)

_ADAPTERS: Dict[str, Type[BurstBackend]] = {}


def _register():
    global _ADAPTERS
    if _ADAPTERS:
        return
    from src.fleet.burst.backends.modal_adapter import ModalBurstBackend
    from src.fleet.burst.backends.ray_adapter import RayBurstBackend
    _ADAPTERS = {
        "modal": ModalBurstBackend,
        "ray": RayBurstBackend,
    }


def _load_enabled() -> List[str]:
    """Return enabled burst backends from config/providers.toml, in declared order."""
    try:
        from src.providers.config import load_config
        cfg = load_config()
        return list(cfg.get("burst_compute", {}).get("enabled", []))
    except Exception as e:
        logger.debug("burst registry: could not load enabled backends (%s)", e)
        return []


def get_backends() -> List[BurstBackend]:
    """Return instantiated burst backends for all enabled + importable adapters.

    Backends whose adapter class cannot be imported (missing dep) are silently
    skipped — the calling code must check ``is_configured()`` before provisioning.
    """
    _register()
    enabled = _load_enabled()
    out: List[BurstBackend] = []
    for key in enabled:
        cls = _ADAPTERS.get(key)
        if cls is None:
            logger.debug("burst registry: no adapter registered for '%s'; skipping", key)
            continue
        try:
            out.append(cls())
        except Exception as e:
            logger.debug("burst registry: could not instantiate '%s': %s", key, e)
    return out


def get_backend(key: str) -> Optional[BurstBackend]:
    """Return a single backend by key, or None if not found/importable."""
    _register()
    cls = _ADAPTERS.get(key)
    if cls is None:
        return None
    try:
        return cls()
    except Exception:
        return None


def first_configured_backend() -> Optional[BurstBackend]:
    """Return the first enabled backend whose is_configured() returns True.

    This is what the BurstManager uses when no explicit backend is requested.
    Returns None when no backend is configured (degrades to local/queue).
    """
    for backend in get_backends():
        try:
            if backend.is_configured():
                return backend
        except Exception:
            pass
    return None
