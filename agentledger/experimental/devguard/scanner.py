"""Public DevGuard dedup API — the "alarm".

Thin, process-friendly wrapper over :class:`devguard.index.DedupIndex` with a
cached singleton so a CLI invocation or a real-time watcher pays the model-load
and index-build cost once.

Typical agent usage (before building a new capability)::

    from agentledger.experimental.devguard.scanner import check_duplicate
    alerts = check_duplicate(proposed_function_source)
    if alerts:
        # An equivalent capability already exists — do NOT rebuild it.
        ...

Alert-only by construction: nothing here mutates code.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Sequence

from .index import DedupIndex, DuplicateAlert

logger = logging.getLogger(__name__)

_INDEX: Optional[DedupIndex] = None


def get_index(*, force: bool = False, rebuild: bool = False) -> DedupIndex:
    """Return the cached DedupIndex, building it on first use or on ``rebuild``."""
    global _INDEX
    if _INDEX is None:
        _INDEX = DedupIndex(force=force).connect()
        if rebuild or _INDEX.count() == 0:
            logger.info("devguard: building dedup index (first use)...")
            _INDEX.build()
    elif rebuild:
        _INDEX.build()
    return _INDEX


def reset_index() -> None:
    """Drop the cached singleton (next call rebuilds). Mainly for tests."""
    global _INDEX
    if _INDEX is not None:
        _INDEX.close()
    _INDEX = None


def index_codebase(
    *, roots: Optional[Sequence[str | Path]] = None, force: bool = False
) -> dict:
    """Build (or rebuild) the index over ``roots`` (default: src/, agentledger/)."""
    idx = DedupIndex(roots=roots, force=force).connect()
    try:
        return idx.build()
    finally:
        idx.close()


def check_duplicate(
    source: str,
    *,
    k: int = 5,
    threshold: float = 0.85,
    exclude_key: Optional[str] = None,
    force: bool = False,
) -> list[DuplicateAlert]:
    """Return duplicate alerts for a proposed function/class/snippet source."""
    return get_index(force=force).check(
        source, k=k, threshold=threshold, exclude_key=exclude_key
    )


def check_file(
    path: str | Path,
    *,
    k: int = 5,
    threshold: float = 0.85,
    force: bool = False,
) -> dict[str, list[DuplicateAlert]]:
    """Check every unit in ``path`` against the index, excluding the unit itself.

    Returns a mapping of ``qualname -> alerts`` for units that have duplicates.
    """
    from .extractor import extract_units_from_file

    idx = get_index(force=force)
    results: dict[str, list[DuplicateAlert]] = {}
    for unit in extract_units_from_file(path):
        alerts = idx.check(
            unit.source, k=k, threshold=threshold, exclude_key=unit.key
        )
        if alerts:
            results[unit.qualname] = alerts
    return results
