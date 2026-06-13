"""Structured error surfacing for the swarm's silent-failure paths.

Several critical paths historically caught all exceptions and only logged a
warning (capability contract violations, run indexing, governance proposal
raising). That prevents cascading failures but also hides bugs operators need
to see. These helpers surface those failures into the Governance Center instead
of letting them disappear into the logs:

  * contract violations become ``SentinelEvent`` rows (visible as alerts), and
  * recoverable-but-noteworthy failures increment durable counters in the
    ``app_settings`` table that the Governance Center dashboard reads.

Every helper here is best-effort: a metrics-store outage must never crash the
swarm path that is merely *reporting* an error, so persistence failures are
swallowed (logged only).
"""
import logging
import os
import time
import uuid
from datetime import datetime, timezone

from src.database import SessionLocal
from src.models import AppSetting, SentinelEvent

logger = logging.getLogger(__name__)

# Counter keys persisted in the app_settings table.
METRIC_FAILED_INDEXES = "metric_failed_indexes"
METRIC_GOVERNANCE_PROPOSAL_FAILURES = "metric_governance_proposal_failures"

# All resettable error counters. Used by reset_metrics() and by callers that
# want to clear every counter at once from the dashboard.
RESETTABLE_METRICS = (
    METRIC_FAILED_INDEXES,
    METRIC_GOVERNANCE_PROPOSAL_FAILURES,
)

# Suffixes recording who last reset a counter and when (stored alongside the
# counter key in app_settings, e.g. "metric_failed_indexes__reset_by").
_RESET_BY_SUFFIX = "__reset_by"
_RESET_AT_SUFFIX = "__reset_at"

# --- Spike alerting -------------------------------------------------------
# A passive cumulative counter only surfaces if an operator opens the
# Governance Center. A sudden *burst* of failures (e.g. a DB outage failing
# every run index) should proactively raise a SentinelEvent so it lands in the
# alerts feed and the open-alerts badge. We detect bursts with a fixed-window
# rate counter (separate from the cumulative counter) and a cooldown so a
# sustained outage raises at most one alert per cooldown, never one per failure.
#
# Window/cooldown state is persisted in app_settings (NOT in process memory) so
# it survives the worker restarts that are common on this platform — otherwise
# every restart during an ongoing outage would re-arm and re-alert.
_WIN_START_SUFFIX = "__alert_win_start"   # epoch seconds the current window opened
_WIN_COUNT_SUFFIX = "__alert_win_count"   # failures counted in the current window
_LAST_ALERT_SUFFIX = "__alert_last_at"    # epoch seconds of the last spike alert

# Defaults are overridable per the governance pillar (configurable, not
# hardwired). app_settings values are mirrored into os.environ at boot/write,
# so an operator can tune these from the settings store without a code change.
# Setting the threshold to <= 0 disables spike alerting entirely.
_DEFAULT_THRESHOLD = 5            # failures within the window that trip an alert
_DEFAULT_WINDOW_SECONDS = 300     # 5 min sliding burst window
_DEFAULT_COOLDOWN_SECONDS = 900   # 15 min minimum gap between repeat alerts

_ENV_THRESHOLD = "SWARM_FAILURE_ALERT_THRESHOLD"
_ENV_WINDOW = "SWARM_FAILURE_ALERT_WINDOW_SECONDS"
_ENV_COOLDOWN = "SWARM_FAILURE_ALERT_COOLDOWN_SECONDS"

# Human-readable alert metadata per failure counter. Only keys listed here
# raise spike alerts; record_failure() on any other key just counts.
_METRIC_ALERT_META = {
    METRIC_FAILED_INDEXES: {
        "alert_type": "failed_index_spike",
        "label": "Run-index persistence",
        "recommended_action": (
            "A burst of run-index failures usually means the results/metrics "
            "store is unreachable or rejecting writes. Check database health "
            "and connectivity, then reset the counter once recovered."
        ),
    },
    METRIC_GOVERNANCE_PROPOSAL_FAILURES: {
        "alert_type": "governance_proposal_failure_spike",
        "label": "Governance-proposal raising",
        "recommended_action": (
            "Repeated failures mean auditor meta-claims are not becoming "
            "votable governance proposals. Inspect the GovernanceEngine path "
            "and the proposal store, then reset the counter once recovered."
        ),
    },
}


def _int_env(name: str, default: int) -> int:
    """Read an integer from the environment, falling back to default."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


def _alert_config():
    """Return (threshold, window_seconds, cooldown_seconds) read live from env.

    Read fresh on every check so operator tuning (env or the settings store,
    which mirrors into os.environ) takes effect without a restart.
    """
    return (
        _int_env(_ENV_THRESHOLD, _DEFAULT_THRESHOLD),
        _int_env(_ENV_WINDOW, _DEFAULT_WINDOW_SECONDS),
        _int_env(_ENV_COOLDOWN, _DEFAULT_COOLDOWN_SECONDS),
    )


def _set_setting(db, key: str, value: str) -> None:
    """Upsert a single app_settings row within an existing session."""
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value


def reset_metric(key: str, operator: str = "") -> bool:
    """Zero an integer counter and record who reset it and when.

    Returns True on success, False if persistence failed. Best-effort like the
    rest of this module, but the caller is told whether it worked so it can
    surface an accurate flash message.

    For an alerting counter this also clears the spike window/cooldown state, so
    once an operator has investigated and reset, a *fresh* outage can alert again
    immediately instead of being suppressed by a stale cooldown.
    """
    db = SessionLocal()
    try:
        _set_setting(db, key, "0")
        _set_setting(db, key + _RESET_BY_SUFFIX, (operator or "operator")[:120])
        _set_setting(db, key + _RESET_AT_SUFFIX, datetime.now(timezone.utc).isoformat())
        if key in _METRIC_ALERT_META:
            _set_setting(db, key + _WIN_START_SUFFIX, "0")
            _set_setting(db, key + _WIN_COUNT_SUFFIX, "0")
            _set_setting(db, key + _LAST_ALERT_SUFFIX, "0")
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        logger.warning("reset_metric(%s) failed: %s", key, e)
        return False
    finally:
        db.close()


def reset_metrics(keys=RESETTABLE_METRICS, operator: str = "") -> bool:
    """Zero several counters at once. Returns True only if all succeeded."""
    return all(reset_metric(key, operator=operator) for key in keys)


def get_last_reset(keys=RESETTABLE_METRICS):
    """Return (reset_at_iso, reset_by) for the most recent counter reset.

    Returns ("", "") if no counter has ever been reset. Best-effort.
    """
    db = SessionLocal()
    try:
        latest_at = ""
        latest_by = ""
        for key in keys:
            at_row = db.query(AppSetting).filter(
                AppSetting.key == key + _RESET_AT_SUFFIX
            ).first()
            if not (at_row and at_row.value):
                continue
            if at_row.value > latest_at:
                latest_at = at_row.value
                by_row = db.query(AppSetting).filter(
                    AppSetting.key == key + _RESET_BY_SUFFIX
                ).first()
                latest_by = (by_row.value if by_row else "") or ""
        return latest_at, latest_by
    except Exception as e:
        logger.warning("get_last_reset failed: %s", e)
        return "", ""
    finally:
        db.close()


def increment_metric(key: str, amount: int = 1) -> None:
    """Atomically bump an integer counter stored in app_settings."""
    db = SessionLocal()
    try:
        row = db.query(AppSetting).filter(AppSetting.key == key).first()
        if row is None:
            db.add(AppSetting(key=key, value=str(amount)))
        else:
            try:
                current = int(row.value or 0)
            except (ValueError, TypeError):
                current = 0
            row.value = str(current + amount)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("increment_metric(%s) failed: %s", key, e)
    finally:
        db.close()


def get_metric(key: str) -> int:
    """Read an integer counter from app_settings (0 if unset/unparseable)."""
    db = SessionLocal()
    try:
        row = db.query(AppSetting).filter(AppSetting.key == key).first()
        if row and row.value is not None:
            try:
                return int(row.value)
            except (ValueError, TypeError):
                return 0
        return 0
    except Exception as e:
        logger.warning("get_metric(%s) failed: %s", key, e)
        return 0
    finally:
        db.close()


def record_sentinel_event(
    alert_type: str,
    severity: str,
    description: str,
    recommended_action: str = "",
    alert_id: str = "",
) -> None:
    """Persist a SentinelEvent row so the failure shows up in the Governance Center."""
    db = SessionLocal()
    try:
        ev = SentinelEvent(
            alert_id=alert_id or f"ALERT-{uuid.uuid4().hex[:8].upper()}",
            alert_type=alert_type,
            severity=severity,
            description=description,
            recommended_action=recommended_action,
        )
        db.add(ev)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("record_sentinel_event(%s) failed: %s", alert_type, e)
    finally:
        db.close()


def _get_setting(db, key: str):
    """Read a single app_settings value within an existing session (None if unset)."""
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    return row.value if row else None


def _get_float(db, key: str, default: float) -> float:
    raw = _get_setting(db, key)
    try:
        return float(raw) if raw is not None else default
    except (ValueError, TypeError):
        return default


def _get_int(db, key: str, default: int) -> int:
    raw = _get_setting(db, key)
    try:
        return int(raw) if raw is not None else default
    except (ValueError, TypeError):
        return default


def _maybe_alert_on_spike(key: str) -> None:
    """Raise a SentinelEvent when failures of ``key`` burst past the threshold.

    Uses a persisted fixed-window counter plus a cooldown so a sustained outage
    raises at most one alert per cooldown rather than one per failure. Only the
    counters in ``_METRIC_ALERT_META`` alert; any other key is a no-op. Wholly
    best-effort: never raises, so a failing alert path can't break the caller.
    """
    meta = _METRIC_ALERT_META.get(key)
    if not meta:
        return  # not an alerting counter — just counted, never alerted

    threshold, window, cooldown = _alert_config()
    if threshold <= 0:
        return  # alerting disabled by configuration

    now = time.time()
    fired_count = None
    db = SessionLocal()
    try:
        win_start = _get_float(db, key + _WIN_START_SUFFIX, 0.0)
        win_count = _get_int(db, key + _WIN_COUNT_SUFFIX, 0)

        # Roll the window forward if the previous one has fully elapsed.
        if now - win_start > window:
            win_start = now
            win_count = 0
        win_count += 1

        _set_setting(db, key + _WIN_START_SUFFIX, repr(win_start))
        _set_setting(db, key + _WIN_COUNT_SUFFIX, str(win_count))

        if win_count >= threshold:
            last_alert = _get_float(db, key + _LAST_ALERT_SUFFIX, 0.0)
            if now - last_alert >= cooldown:
                # Fire, then re-arm: record the alert time and reset the window
                # so the next alert needs a fresh burst AND the cooldown to pass.
                _set_setting(db, key + _LAST_ALERT_SUFFIX, repr(now))
                _set_setting(db, key + _WIN_START_SUFFIX, repr(now))
                _set_setting(db, key + _WIN_COUNT_SUFFIX, "0")
                fired_count = win_count

        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("_maybe_alert_on_spike(%s) failed: %s", key, e)
        return
    finally:
        db.close()

    if fired_count is not None:
        record_sentinel_event(
            alert_type=meta["alert_type"],
            severity="high",
            description=(
                f"{meta['label']} failures spiked: {fired_count} failures "
                f"within {window}s (threshold {threshold}). The cumulative "
                f"'{key}' counter is still climbing."
            ),
            recommended_action=meta["recommended_action"],
        )


def record_failure(key: str, amount: int = 1) -> None:
    """Governed entry point for the swarm's recoverable-but-noteworthy failures.

    Increments the durable cumulative counter (so the Governance Center
    dashboard keeps showing the running total) AND raises a proactive
    SentinelEvent when failures of this counter burst past the configured
    threshold within the window. Silent-failure paths should call this instead
    of ``increment_metric`` directly. Best-effort throughout.
    """
    increment_metric(key, amount)
    try:
        _maybe_alert_on_spike(key)
    except Exception as e:  # defensive: spike-check must never break the caller
        logger.warning("record_failure spike-check(%s) failed: %s", key, e)
