"""Tests for local-node recovery alerting (HealthMonitor._alert_on_local_offline).

When a Mesh-connected local model node (Ollama/MLX) drops, the HealthMonitor
raises a ``local_node_offline`` SentinelEvent. This suite covers the recovery
half of that loop: when the node comes back up, the open offline alert must be
auto-acknowledged and a one-time informational ``local_node_recovered`` notice
posted, so operators get positive confirmation in the live governance view
rather than only a log line.

The logic runs against a throwaway SQLite DB (both ``src.database.SessionLocal``
— used by the monitor's own queries — and ``error_metrics.SessionLocal`` — used
by ``record_sentinel_event`` — are patched onto it), so no Postgres, network, or
running swarm is needed. Reachability is driven by feeding ``run_checks`` results
directly into ``_alert_on_local_offline``.

Runnable two ways:
  * ``python tests/test_local_node_recovery.py``  (standalone, non-zero on failure)
  * ``pytest tests/test_local_node_recovery.py``   (test_* functions; no plugins)
"""
import asyncio
import os
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import src.database as db_mod
import src.swarm.error_metrics as em
from src.models import Base, SentinelEvent
from src.selfheal.health_monitor import HealthMonitor


@contextmanager
def _fresh_db():
    """Patch the DB session factories onto an empty file-backed SQLite DB.

    File-backed (not :memory:) so the many short-lived sessions opened across
    threads (asyncio.to_thread offloads the DB work) all see the same data.
    Both ``src.database.SessionLocal`` and ``error_metrics.SessionLocal`` are
    repointed and restored afterwards.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    orig_db, orig_em = db_mod.SessionLocal, em.SessionLocal
    db_mod.SessionLocal = TestSession
    em.SessionLocal = TestSession
    try:
        yield TestSession
    finally:
        db_mod.SessionLocal = orig_db
        em.SessionLocal = orig_em
        engine.dispose()
        try:
            os.remove(path)
        except OSError:
            pass


def _node(provider="ollama", up=True, endpoint="http://10.0.0.5:11434"):
    return {"provider": provider, "server_up": up, "endpoint": endpoint, "exec_locus": "local"}


def _info(nodes):
    return {"nodes": nodes}


def _alerts(alert_type=None):
    db = em.SessionLocal()
    try:
        q = db.query(SentinelEvent)
        if alert_type is not None:
            q = q.filter(SentinelEvent.alert_type == alert_type)
        return q.order_by(SentinelEvent.id.asc()).all()
    finally:
        db.close()


def _run(coro):
    return asyncio.run(coro)


def test_recovery_acks_offline_and_posts_notice():
    with _fresh_db():
        hm = HealthMonitor()
        # Node goes down -> offline alert raised, unacknowledged.
        _run(hm._alert_on_local_offline(_info([_node(up=False)])))
        offline = _alerts("local_node_offline")
        assert len(offline) == 1, f"one offline alert expected, got {len(offline)}"
        assert offline[0].acknowledged is False

        # Node comes back up -> offline alert auto-acked + recovery notice posted.
        _run(hm._alert_on_local_offline(_info([_node(up=True)])))
        offline = _alerts("local_node_offline")
        assert offline[0].acknowledged is True, "offline alert must be auto-acknowledged on recovery"
        recovered = _alerts("local_node_recovered")
        assert len(recovered) == 1, f"one recovery notice expected, got {len(recovered)}"
        ev = recovered[0]
        assert ev.severity == "info"
        assert ev.acknowledged is False, "recovery notice should surface as an open alert"
        assert ev.alert_id == "LOCAL-RECOVERED-ollama"
        assert "back online" in (ev.description or "")


def test_recovery_notice_is_one_time_not_per_tick():
    with _fresh_db():
        hm = HealthMonitor()
        _run(hm._alert_on_local_offline(_info([_node(up=False)])))
        _run(hm._alert_on_local_offline(_info([_node(up=True)])))
        # Several more healthy ticks must not keep posting recovery notices.
        for _ in range(3):
            _run(hm._alert_on_local_offline(_info([_node(up=True)])))
        assert len(_alerts("local_node_recovered")) == 1, "recovery notice must fire exactly once per cycle"


def test_recovery_fires_after_process_restart_lost_inmemory_state():
    """A restart clears the in-memory transition map, but the open DB offline
    row must still drive a recovery once the node is seen up again."""
    with _fresh_db():
        hm = HealthMonitor()
        _run(hm._alert_on_local_offline(_info([_node(up=False)])))
        assert _alerts("local_node_offline")[0].acknowledged is False

        # Simulate a fresh process: brand new monitor, empty _previous_local_status.
        hm2 = HealthMonitor()
        _run(hm2._alert_on_local_offline(_info([_node(up=True)])))
        assert _alerts("local_node_offline")[0].acknowledged is True
        assert len(_alerts("local_node_recovered")) == 1


def test_healthy_node_with_no_open_alert_posts_nothing():
    with _fresh_db():
        hm = HealthMonitor()
        # Node has always been up: no offline alert, so no recovery notice.
        _run(hm._alert_on_local_offline(_info([_node(up=True)])))
        _run(hm._alert_on_local_offline(_info([_node(up=True)])))
        assert _alerts() == [], "a node that never went down must not generate alerts"


def test_second_drop_after_recovery_raises_fresh_offline_alert():
    """The recovery notice uses a distinct alert_id so it never masks a later
    real drop from the offline-dedup guard."""
    with _fresh_db():
        hm = HealthMonitor()
        _run(hm._alert_on_local_offline(_info([_node(up=False)])))   # drop 1
        _run(hm._alert_on_local_offline(_info([_node(up=True)])))    # recover
        _run(hm._alert_on_local_offline(_info([_node(up=False)])))   # drop 2
        offline = _alerts("local_node_offline")
        assert len(offline) == 2, f"second drop must raise a fresh offline alert, got {len(offline)}"
        # The newest offline alert is open again.
        assert offline[-1].acknowledged is False


def test_per_node_isolation():
    """Recovering one node must not touch another node's open offline alert."""
    with _fresh_db():
        hm = HealthMonitor()
        _run(hm._alert_on_local_offline(_info([
            _node(provider="ollama", up=False),
            _node(provider="mlx", up=False),
        ])))
        assert len(_alerts("local_node_offline")) == 2
        # Only ollama recovers.
        _run(hm._alert_on_local_offline(_info([
            _node(provider="ollama", up=True),
            _node(provider="mlx", up=False),
        ])))
        by_alert = {a.alert_id: a for a in _alerts("local_node_offline")}
        assert by_alert["LOCAL-OFFLINE-ollama"].acknowledged is True
        assert by_alert["LOCAL-OFFLINE-mlx"].acknowledged is False
        recovered = _alerts("local_node_recovered")
        assert len(recovered) == 1
        assert recovered[0].alert_id == "LOCAL-RECOVERED-ollama"


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
