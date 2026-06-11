"""DevGuard real-time watcher — fires dedup alerts as you edit.

Optional: requires ``watchdog`` (a dev-only dependency installed via
scripts/post-merge.sh). Alert-only: on each ``.py`` create / modify it
(1) reports duplicates of the changed units against the live index and
(2) refreshes the index entry for that file. It never edits your code.

Run it::

    python -m agentledger.experimental.devguard.watcher [<root> ...]
    # or
    python -m agentledger.experimental.devguard.cli watch [<root> ...]
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional, Sequence

from .alerts import AlertSink, default_sink
from .extractor import extract_units_from_file
from .index import _default_roots
from .scanner import get_index

logger = logging.getLogger(__name__)

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    _HAS_WATCHDOG = True
except ImportError:  # keep module importable when the dev dep is absent
    FileSystemEventHandler = object  # type: ignore
    Observer = None  # type: ignore
    _HAS_WATCHDOG = False


class _DedupEventHandler(FileSystemEventHandler):
    """Check changed .py files for duplicates, then refresh their index rows."""

    def __init__(
        self,
        index,
        sink: AlertSink,
        *,
        threshold: float = 0.85,
        debounce: float = 0.5,
    ):
        self.index = index
        self.sink = sink
        self.threshold = threshold
        self.debounce = debounce
        self._last: dict[str, float] = {}

    # -- watchdog callbacks --------------------------------------------- #
    def on_created(self, event) -> None:
        if not event.is_directory:
            self._handle(event.src_path)

    def on_modified(self, event) -> None:
        if not event.is_directory:
            self._handle(event.src_path)

    def on_moved(self, event) -> None:
        if not event.is_directory:
            self._handle(getattr(event, "dest_path", event.src_path))

    # -- core ------------------------------------------------------------ #
    def _debounced(self, path: str) -> bool:
        now = time.monotonic()
        if now - self._last.get(path, 0.0) < self.debounce:
            return True
        self._last[path] = now
        return False

    def _handle(self, src_path: str) -> int:
        if not src_path.endswith(".py") or self._debounced(src_path):
            return 0
        path = Path(src_path)
        if not path.exists():
            return 0
        try:
            units = extract_units_from_file(path)
        except (SyntaxError, UnicodeDecodeError):
            return 0  # mid-edit / not valid yet — skip quietly
        found = 0
        for unit in units:
            # exclude_key drops this unit's own (stale) indexed copy.
            alerts = self.index.check(
                unit.source, threshold=self.threshold, exclude_key=unit.key
            )
            if alerts:
                found += len(alerts)
                self.sink.emit(alerts, source=f"{path}::{unit.qualname}")
        # keep the index current so later edits compare against the latest source
        self.index.reindex_file(path)
        return found


def watch(
    roots: Optional[Sequence[str | Path]] = None,
    *,
    threshold: float = 0.85,
    sink: Optional[AlertSink] = None,
    force: bool = False,
) -> int:
    """Block and watch ``roots`` (default src/, agentledger/) for near-duplicates."""
    if not _HAS_WATCHDOG:
        raise SystemExit(
            "watchdog is not installed; it is a dev-only dependency "
            "(see scripts/post-merge.sh). Install it to use the DevGuard watcher."
        )
    index = get_index(force=force)
    sink = sink or default_sink()
    candidates = [Path(r) for r in roots] if roots else _default_roots()
    watched = [r for r in candidates if r.exists()]
    if not watched:
        raise SystemExit("no existing roots to watch")
    handler = _DedupEventHandler(index, sink, threshold=threshold)
    observer = Observer()
    for root in watched:
        observer.schedule(handler, str(root), recursive=True)
    observer.start()
    banner = "devguard watcher: watching " + ", ".join(str(r) for r in watched)
    logger.info(banner)
    print(banner)
    print("(alert-only; Ctrl-C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(watch(sys.argv[1:] or None))
