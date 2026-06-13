"""Tests for proactive swarm-failure spike alerting (error_metrics.record_failure).

The swarm's recoverable-but-noteworthy failure paths (run-index persistence,
governance-proposal raising) increment durable cumulative counters. Those
counters are passive — an operator only sees them in the Governance Center.
``record_failure`` adds a proactive layer: when failures of a counter burst
past a configured threshold within a window, a ``SentinelEvent`` is raised so
the spike lands in the alerts feed / open-alerts badge. A cooldown keeps a
sustained outage from spamming one alert per failure.

These tests exercise that logic in isolation against a throwaway SQLite DB
(error_metrics.SessionLocal is patched), so no Postgres, network, or running
swarm is needed. Thresholds/window/cooldown are driven via the documented env
vars, and time-dependent state (window start / last-alert) is aged by writing
the persisted app_settings rows directly — no sleeping.

Runnable two ways:
  * ``python tests/test_swarm_failure_alerts.py``  (standalone, non-zero on failure)
  * ``pytest tests/test_swarm_failure_alerts.py``   (test_* functions; no plugins)
"""
import os
import sys
import time
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.swarm.error_metrics as em
from src.models import Base, AppSetting, SentinelEvent


@contextmanager
def _fresh_db():
    """Patch error_metrics.SessionLocal onto an empty file-backed SQLite DB.

    File-backed (not :memory:) so the many short-lived sessions the module opens
    all see the same data. Restores the real SessionLocal afterwards.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    original = em.SessionLocal
    em.SessionLocal = TestSession
    try:
        yield TestSession
    finally:
        em.SessionLocal = original
        engine.dispose()
        try:
            os.remove(path)
        except OSError:
            pass


@contextmanager
def _env(**overrides):
    """Temporarily set/unset env vars, restoring prior state afterwards."""
    saved = {k: os.environ.get(k) for k in overrides}
    try:
        for k, v in overrides.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _alerts(alert_type=None):
    db = em.SessionLocal()
    try:
        q = db.query(SentinelEvent)
        if alert_type is not None:
            q = q.filter(SentinelEvent.alert_type == alert_type)
        return q.all()
    finally:
        db.close()


def _set_raw(key, value):
    """Directly upsert an app_settings row (used to age window/last-alert state)."""
    db = em.SessionLocal()
    try:
        em._set_setting(db, key, str(value))
        db.commit()
    finally:
        db.close()


KEY = em.METRIC_FAILED_INDEXES


def test_below_threshold_does_not_alert():
    with _fresh_db(), _env(
        SWARM_FAILURE_ALERT_THRESHOLD=5,
        SWARM_FAILURE_ALERT_WINDOW_SECONDS=300,
        SWARM_FAILURE_ALERT_COOLDOWN_SECONDS=900,
    ):
        for _ in range(4):
            em.record_failure(KEY)
        assert em.get_metric(KEY) == 4, "cumulative counter must still climb"
        assert _alerts() == [], "no alert below threshold"


def test_crossing_threshold_alerts_once():
    with _fresh_db(), _env(
        SWARM_FAILURE_ALERT_THRESHOLD=5,
        SWARM_FAILURE_ALERT_WINDOW_SECONDS=300,
        SWARM_FAILURE_ALERT_COOLDOWN_SECONDS=900,
    ):
        for _ in range(5):
            em.record_failure(KEY)
        alerts = _alerts()
        assert len(alerts) == 1, f"exactly one spike alert, got {len(alerts)}"
        ev = alerts[0]
        assert ev.alert_type == "failed_index_spike"
        assert ev.severity == "high"
        assert ev.acknowledged is False
        assert KEY in (ev.description or "")
        assert em.get_metric(KEY) == 5


def test_sustained_burst_within_cooldown_does_not_respam():
    with _fresh_db(), _env(
        SWARM_FAILURE_ALERT_THRESHOLD=5,
        SWARM_FAILURE_ALERT_WINDOW_SECONDS=300,
        SWARM_FAILURE_ALERT_COOLDOWN_SECONDS=900,
    ):
        for _ in range(20):  # 4x the threshold, all within window + cooldown
            em.record_failure(KEY)
        assert len(_alerts()) == 1, "cooldown must suppress repeat alerts"
        assert em.get_metric(KEY) == 20


def test_window_rollover_resets_the_burst_counter():
    with _fresh_db(), _env(
        SWARM_FAILURE_ALERT_THRESHOLD=3,
        SWARM_FAILURE_ALERT_WINDOW_SECONDS=300,
        SWARM_FAILURE_ALERT_COOLDOWN_SECONDS=900,
    ):
        em.record_failure(KEY)
        em.record_failure(KEY)  # window count == 2, below threshold 3
        assert _alerts() == []
        # Age the window so the next failure opens a fresh window.
        _set_raw(KEY + em._WIN_START_SUFFIX, time.time() - 1000)
        em.record_failure(KEY)  # fresh window, count resets to 1
        assert _alerts() == [], "stale failures must not accumulate across windows"


def test_realert_after_cooldown_elapses():
    with _fresh_db(), _env(
        SWARM_FAILURE_ALERT_THRESHOLD=3,
        SWARM_FAILURE_ALERT_WINDOW_SECONDS=300,
        SWARM_FAILURE_ALERT_COOLDOWN_SECONDS=300,
    ):
        for _ in range(3):
            em.record_failure(KEY)
        assert len(_alerts()) == 1
        # Simulate the cooldown having elapsed since the first alert.
        _set_raw(KEY + em._LAST_ALERT_SUFFIX, time.time() - 1000)
        for _ in range(3):
            em.record_failure(KEY)
        assert len(_alerts()) == 2, "a fresh burst after cooldown must re-alert"


def test_threshold_zero_disables_alerting():
    with _fresh_db(), _env(
        SWARM_FAILURE_ALERT_THRESHOLD=0,
        SWARM_FAILURE_ALERT_WINDOW_SECONDS=300,
        SWARM_FAILURE_ALERT_COOLDOWN_SECONDS=900,
    ):
        for _ in range(50):
            em.record_failure(KEY)
        assert _alerts() == [], "threshold<=0 disables alerting"
        assert em.get_metric(KEY) == 50, "counter still increments when alerting off"


def test_governance_counter_has_its_own_alert_type():
    gkey = em.METRIC_GOVERNANCE_PROPOSAL_FAILURES
    with _fresh_db(), _env(
        SWARM_FAILURE_ALERT_THRESHOLD=3,
        SWARM_FAILURE_ALERT_WINDOW_SECONDS=300,
        SWARM_FAILURE_ALERT_COOLDOWN_SECONDS=900,
    ):
        for _ in range(3):
            em.record_failure(gkey)
        alerts = _alerts()
        assert len(alerts) == 1
        assert alerts[0].alert_type == "governance_proposal_failure_spike"


def test_reset_clears_spike_state_so_fresh_outage_realerts():
    with _fresh_db(), _env(
        SWARM_FAILURE_ALERT_THRESHOLD=3,
        SWARM_FAILURE_ALERT_WINDOW_SECONDS=300,
        SWARM_FAILURE_ALERT_COOLDOWN_SECONDS=900,
    ):
        for _ in range(3):
            em.record_failure(KEY)
        assert len(_alerts()) == 1
        # Operator investigates and resets the counter. The lingering cooldown
        # must NOT suppress a fresh outage that starts right after.
        assert em.reset_metric(KEY, operator="alice") is True
        assert em.get_metric(KEY) == 0
        for _ in range(3):
            em.record_failure(KEY)
        assert len(_alerts()) == 2, "reset must clear spike state so a fresh burst re-alerts"


def test_unknown_counter_never_alerts():
    with _fresh_db(), _env(
        SWARM_FAILURE_ALERT_THRESHOLD=2,
        SWARM_FAILURE_ALERT_WINDOW_SECONDS=300,
        SWARM_FAILURE_ALERT_COOLDOWN_SECONDS=900,
    ):
        for _ in range(10):
            em.record_failure("metric_some_other_counter")
        assert _alerts() == [], "only known failure counters raise spike alerts"
        assert em.get_metric("metric_some_other_counter") == 10


def _main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
