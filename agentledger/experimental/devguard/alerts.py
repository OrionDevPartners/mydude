"""DevGuard alert sinks — where duplicate alerts are reported.

Alert-only by construction: a sink *reports*, it never mutates, merges, or
synthesizes code. The sink seam keeps the reporting target swappable
(provider-agnostic pillar):

* :class:`ConsoleAlertSink` — human-readable, for the CLI / dev loop.
* :class:`JsonlAlertSink` — append-only ``.devguard/alerts.jsonl`` audit trail.
* :class:`MultiSink` — fan-out to several sinks, isolating failures.

Richer surfaces (the in-app Governance / Sentinel feed) plug in later as
additional sinks implementing the same :class:`AlertSink` protocol, without
touching any caller.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol, Sequence, TextIO, runtime_checkable

from .index import DuplicateAlert

logger = logging.getLogger(__name__)


def _default_alerts_path() -> Path:
    env = os.environ.get("DEVGUARD_ALERTS_PATH")
    if env:
        return Path(env)
    # alerts.py -> devguard -> experimental -> agentledger -> repo root
    return Path(__file__).resolve().parents[3] / ".devguard" / "alerts.jsonl"


@runtime_checkable
class AlertSink(Protocol):
    """A reporting target for duplicate alerts."""

    def emit(
        self, alerts: Sequence[DuplicateAlert], *, source: Optional[str] = None
    ) -> None:
        ...


class ConsoleAlertSink:
    """Print alerts in a concise, human-readable form."""

    def __init__(self, stream: Optional[TextIO] = None, *, quiet_when_clean: bool = False):
        self.stream = stream if stream is not None else sys.stdout
        self.quiet_when_clean = quiet_when_clean

    def emit(
        self, alerts: Sequence[DuplicateAlert], *, source: Optional[str] = None
    ) -> None:
        label = source or "snippet"
        if not alerts:
            if not self.quiet_when_clean:
                print(f"[OK] no duplicates for {label}", file=self.stream)
            return
        print(f"[DUP] {len(alerts)} alert(s) for {label}", file=self.stream)
        for a in alerts:
            print(f"      {a}", file=self.stream)


class JsonlAlertSink:
    """Append each alert as one JSON line — a durable, dependency-free audit trail."""

    def __init__(self, path: Optional[str | Path] = None):
        self.path = Path(path) if path is not None else _default_alerts_path()

    def emit(
        self, alerts: Sequence[DuplicateAlert], *, source: Optional[str] = None
    ) -> None:
        if not alerts:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat()
        with self.path.open("a", encoding="utf-8") as fh:
            for a in alerts:
                rec = {"ts": ts, "source": source, **a.to_dict()}
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


class MultiSink:
    """Fan-out to several sinks; one sink failing never silences the others."""

    def __init__(self, *sinks: AlertSink):
        self.sinks = [s for s in sinks if s is not None]

    def emit(
        self, alerts: Sequence[DuplicateAlert], *, source: Optional[str] = None
    ) -> None:
        for sink in self.sinks:
            try:
                sink.emit(alerts, source=source)
            except Exception:  # noqa: BLE001 - one sink must not break the rest
                logger.exception("devguard: alert sink %r failed", sink)


class SentinelAlertSink:
    """Surface duplicate alerts in the in-app Governance Center.

    Reuses ``src.swarm.error_metrics.record_sentinel_event`` (best-effort) so
    the dedup alarm reaches operators on the real in-app surface — the
    ``SentinelEvent`` feed — not just the console/JSONL trail. Emits ONE
    aggregated row per check (mirrors the broker's contract_violation pattern),
    never one row per alert. Still alert-only: a SentinelEvent is a
    notification, never a code change.
    """

    def __init__(self, *, alert_type: str = "devguard_duplicate", max_listed: int = 5):
        self.alert_type = alert_type
        self.max_listed = max_listed

    def emit(
        self, alerts: Sequence[DuplicateAlert], *, source: Optional[str] = None
    ) -> None:
        if not alerts:
            return
        try:
            from src.swarm.error_metrics import record_sentinel_event
        except Exception:  # noqa: BLE001 - surface optional; never break the caller
            logger.warning("devguard: Sentinel surface unavailable; alert not recorded")
            return

        label = source or "snippet"
        severe = any(a.match_type in ("exact", "structural") for a in alerts)
        listed = "; ".join(str(a) for a in alerts[: self.max_listed])
        more = (
            "" if len(alerts) <= self.max_listed
            else f" (+{len(alerts) - self.max_listed} more)"
        )
        description = (
            f"DevGuard found {len(alerts)} existing implementation(s) matching "
            f"{label}: {listed}{more}"
        )
        try:
            record_sentinel_event(
                alert_type=self.alert_type,
                severity="warning" if severe else "info",
                description=description,
                recommended_action=(
                    "An equivalent capability already exists — reuse it instead "
                    "of rebuilding. DevGuard is alert-only; no code was changed."
                ),
            )
        except Exception:  # noqa: BLE001
            logger.exception("devguard: failed to record Sentinel duplicate alert")


def default_sink(
    *, console: bool = True, jsonl: bool = True, sentinel: bool = False
) -> MultiSink:
    """Build the standard sink: console output + JSONL audit trail.

    Set ``sentinel=True`` to also surface alerts in the in-app Governance
    Center (used by the live capability-request path, off by default for CLI).
    """
    sinks: list[AlertSink] = []
    if console:
        sinks.append(ConsoleAlertSink())
    if jsonl:
        sinks.append(JsonlAlertSink())
    if sentinel:
        sinks.append(SentinelAlertSink())
    return MultiSink(*sinks)
