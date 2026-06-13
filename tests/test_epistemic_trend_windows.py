"""Tests for the windowed epistemic-trend filtering (Governance page).

The Governance trend chart + summary cards are driven by
``_epistemic_trend(db, window=...)`` in ``src/web/routes_governance.py``. The
operator can pick a window that is either a *run-count* ("10"/"30"/"100" — the
most recent N indexed runs) or a *date-range* ("24h"/"7d"/"30d" — every run
inside a rolling period). Unknown keys fall back to the default. Critically,
BOTH the per-run trend AND the summary totals/ratios recompute over the SAME
windowed set, so the cards change when the window changes. This was verified by
hand only; this suite locks it in.

Covered:
  * ``_resolve_window`` — every defined key resolves, unknown / empty / None
    fall back to the default ("30"), and the default itself is well-formed.
  * ``_epistemic_trend`` count windows (10/30/100): the most-recent-N slice, the
    chronological (ascending) point order, the per-point shape, and that totals /
    ratios / run_count recompute over only the windowed rows.
  * ``_epistemic_trend`` range windows (24h/7d/30d): only rows inside the rolling
    period are included; older rows are excluded; the boundary grows with window.
  * unknown-key fallback produces the same result as the default window.
  * the ``GET /api/governance/epistemic-trend?window=`` endpoint returns the full
    shape with ``points[].created_at`` serialized to ISO strings (auth-cookie /
    no-startup TestClient harness, with SessionLocal pointed at an isolated DB).

The window logic takes ``db`` as a parameter and only calls
``db.query(SwarmRunIndex)``, so we drive it with a throwaway in-memory SQLite
session seeded with known rows — fully deterministic and independent of the dev
database's contents.

Runnable two ways:
  * ``python tests/test_epistemic_trend_windows.py``  (standalone; non-zero exit on failure)
  * ``pytest tests/test_epistemic_trend_windows.py``    (test_* functions; no plugins needed)
"""
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.models import SwarmRunIndex
from src.web.routes_governance import (
    DEFAULT_EPISTEMIC_WINDOW,
    EPISTEMIC_LABELS,
    EPISTEMIC_WINDOWS,
    _epistemic_trend,
    _resolve_window,
)


# --------------------------------------------------------------------------- #
# In-memory DB harness (only the swarm_run_index table is created)
# --------------------------------------------------------------------------- #

def _make_session_factory():
    """A throwaway, single-connection in-memory SQLite DB with just the
    ``swarm_run_index`` table created. StaticPool keeps every session on the same
    connection so seeded rows are visible across sessions."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SwarmRunIndex.__table__.create(bind=engine)
    return sessionmaker(bind=engine)


def _seed(db, rows):
    """rows: list of (run_id, created_at, epistemic_dict[, aborted])."""
    for row in rows:
        run_id = row[0]
        created_at = row[1]
        ep = row[2]
        aborted = row[3] if len(row) > 3 else False
        db.add(SwarmRunIndex(
            run_id=run_id,
            goal="goal-%s" % run_id,
            epistemic_summary_json=json.dumps(ep),
            created_at=created_at,
            aborted=aborted,
        ))
    db.commit()


# --------------------------------------------------------------------------- #
# 1. _resolve_window
# --------------------------------------------------------------------------- #

def test_resolve_window_returns_each_defined_key():
    for w in EPISTEMIC_WINDOWS:
        assert _resolve_window(w["key"]) is w, w["key"]


def test_resolve_window_unknown_falls_back_to_default():
    spec = _resolve_window("nope-not-a-window")
    assert spec["key"] == DEFAULT_EPISTEMIC_WINDOW, spec


def test_resolve_window_empty_and_none_fall_back_to_default():
    for bad in ("", None):
        spec = _resolve_window(bad)
        assert spec["key"] == DEFAULT_EPISTEMIC_WINDOW, (bad, spec)


def test_default_window_is_well_formed():
    spec = _resolve_window(DEFAULT_EPISTEMIC_WINDOW)
    assert spec["key"] == DEFAULT_EPISTEMIC_WINDOW
    assert spec["mode"] in ("count", "range"), spec
    if spec["mode"] == "count":
        assert isinstance(spec["count"], int) and spec["count"] > 0, spec


# --------------------------------------------------------------------------- #
# 2. _epistemic_trend — count windows (10 / 30 / 100)
# --------------------------------------------------------------------------- #

def _seed_count_dataset(db):
    """15 rows. The 5 OLDEST carry a huge verified count; the 10 newest carry a
    small one. Every row carries unknown=1. This makes the windowed totals
    differ sharply between the "last 10 runs" and "last 30 runs" windows so we
    can prove the summary recomputes over only the windowed set."""
    base = datetime(2026, 1, 1, 0, 0, 0)
    rows = []
    for i in range(15):
        verified = 100 if i < 5 else 1  # i 0..4 are oldest (earliest created_at)
        rows.append((
            "run-%02d" % i,
            base + timedelta(minutes=i),
            {"verified": verified, "derived": 0, "hypothesis": 0, "unknown": 1},
        ))
    _seed(db, rows)


def test_count_window_10_takes_only_most_recent_ten():
    Session = _make_session_factory()
    db = Session()
    try:
        _seed_count_dataset(db)
        trend = _epistemic_trend(db, window="10")
    finally:
        db.close()
    assert trend["window"] == "10", trend["window"]
    assert trend["run_count"] == 10, trend["run_count"]
    assert len(trend["points"]) == 10
    # The newest 10 are runs 05..14 — none of the verified=100 outliers (00..04).
    run_ids = {p["run_id"] for p in trend["points"]}
    assert run_ids == {"run-%02d" % i for i in range(5, 15)}, run_ids
    # Totals recompute over ONLY those 10 rows: verified = 10*1, unknown = 10*1.
    assert trend["totals"]["verified"] == 10, trend["totals"]
    assert trend["totals"]["unknown"] == 10, trend["totals"]
    assert trend["grand_total"] == 20, trend["grand_total"]
    assert trend["verified_ratio"] == 50.0, trend["verified_ratio"]
    assert trend["unknown_ratio"] == 50.0, trend["unknown_ratio"]


def test_count_window_30_includes_all_and_recomputes_summary():
    Session = _make_session_factory()
    db = Session()
    try:
        _seed_count_dataset(db)
        trend = _epistemic_trend(db, window="30")
    finally:
        db.close()
    # Only 15 rows exist, so the "last 30" window holds all of them...
    assert trend["run_count"] == 15, trend["run_count"]
    # ...and the verified=100 outliers now dominate, proving the summary math
    # recomputes over the windowed set rather than being fixed.
    assert trend["totals"]["verified"] == 5 * 100 + 10 * 1, trend["totals"]
    assert trend["totals"]["unknown"] == 15, trend["totals"]
    assert trend["grand_total"] == 525, trend["grand_total"]
    assert trend["verified_ratio"] == round(510 / 525 * 100, 1), trend["verified_ratio"]
    # The window-10 ratio (50.0) and the window-30 ratio differ -> recompute.
    assert trend["verified_ratio"] != 50.0


def test_count_window_100_caps_at_available_rows():
    Session = _make_session_factory()
    db = Session()
    try:
        _seed_count_dataset(db)
        trend = _epistemic_trend(db, window="100")
    finally:
        db.close()
    assert trend["window"] == "100"
    assert trend["run_count"] == 15, trend["run_count"]


def test_points_are_chronological_and_well_shaped():
    Session = _make_session_factory()
    db = Session()
    try:
        _seed_count_dataset(db)
        trend = _epistemic_trend(db, window="100")
    finally:
        db.close()
    points = trend["points"]
    # Ascending by created_at (the query is DESC then reversed).
    times = [p["created_at"] for p in points]
    assert times == sorted(times), "points not chronological"
    # Each point carries the full, stable shape the chart depends on.
    for p in points:
        assert set(p.keys()) >= {"run_id", "created_at", "counts", "total", "pct", "aborted"}, p
        assert set(p["counts"].keys()) == set(EPISTEMIC_LABELS), p["counts"]
        assert set(p["pct"].keys()) == set(EPISTEMIC_LABELS), p["pct"]
        assert p["total"] == sum(p["counts"].values()), p


def test_empty_db_yields_zeroed_summary():
    Session = _make_session_factory()
    db = Session()
    try:
        trend = _epistemic_trend(db, window="30")
    finally:
        db.close()
    assert trend["run_count"] == 0
    assert trend["points"] == []
    assert trend["grand_total"] == 0
    assert trend["verified_ratio"] == 0
    assert trend["unknown_ratio"] == 0
    assert trend["totals"] == {label: 0 for label in EPISTEMIC_LABELS}


# --------------------------------------------------------------------------- #
# 3. _epistemic_trend — date-range windows (24h / 7d / 30d)
# --------------------------------------------------------------------------- #

def _seed_range_dataset(db):
    """Rows spread across time relative to 'now' so each range window admits a
    strictly larger slice. The 40-day-old row is outside every window."""
    now = datetime.utcnow()
    rows = [
        ("r-1h", now - timedelta(hours=1), {"verified": 1, "unknown": 1}),
        ("r-2h", now - timedelta(hours=2), {"verified": 1, "unknown": 1}),
        ("r-2d", now - timedelta(days=2), {"verified": 1, "unknown": 1}),
        ("r-10d", now - timedelta(days=10), {"verified": 1, "unknown": 1}),
        ("r-40d", now - timedelta(days=40), {"verified": 1, "unknown": 1}),
    ]
    _seed(db, rows)


def test_range_window_24h_only_last_day():
    Session = _make_session_factory()
    db = Session()
    try:
        _seed_range_dataset(db)
        trend = _epistemic_trend(db, window="24h")
    finally:
        db.close()
    assert trend["window"] == "24h"
    assert {p["run_id"] for p in trend["points"]} == {"r-1h", "r-2h"}, trend["points"]
    assert trend["run_count"] == 2


def test_range_window_7d_includes_recent_week():
    Session = _make_session_factory()
    db = Session()
    try:
        _seed_range_dataset(db)
        trend = _epistemic_trend(db, window="7d")
    finally:
        db.close()
    assert {p["run_id"] for p in trend["points"]} == {"r-1h", "r-2h", "r-2d"}, trend["points"]
    assert trend["run_count"] == 3


def test_range_window_30d_includes_recent_month_but_not_older():
    Session = _make_session_factory()
    db = Session()
    try:
        _seed_range_dataset(db)
        trend = _epistemic_trend(db, window="30d")
    finally:
        db.close()
    ids = {p["run_id"] for p in trend["points"]}
    assert ids == {"r-1h", "r-2h", "r-2d", "r-10d"}, ids
    assert "r-40d" not in ids  # 40 days old -> outside the 30-day window
    assert trend["run_count"] == 4


# --------------------------------------------------------------------------- #
# 4. Unknown-key fallback parity
# --------------------------------------------------------------------------- #

def test_unknown_window_key_matches_default_window():
    Session = _make_session_factory()
    db = Session()
    try:
        _seed_count_dataset(db)
        fallback = _epistemic_trend(db, window="totally-unknown")
        default = _epistemic_trend(db, window=DEFAULT_EPISTEMIC_WINDOW)
    finally:
        db.close()
    assert fallback["window"] == DEFAULT_EPISTEMIC_WINDOW, fallback["window"]
    assert fallback["run_count"] == default["run_count"]
    assert fallback["totals"] == default["totals"]
    assert fallback["grand_total"] == default["grand_total"]
    assert fallback["verified_ratio"] == default["verified_ratio"]
    assert [p["run_id"] for p in fallback["points"]] == [p["run_id"] for p in default["points"]]


# --------------------------------------------------------------------------- #
# 5. API endpoint: GET /api/governance/epistemic-trend?window=
# --------------------------------------------------------------------------- #

@contextmanager
def _env(**overrides):
    saved = {k: os.environ.get(k) for k in overrides}
    try:
        for k, v in overrides.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextmanager
def _patched_session_factory(factory):
    """Point ``src.database.SessionLocal`` at our seeded in-memory DB for the
    duration of the call. The endpoint does ``from src.database import
    SessionLocal`` at call time, so swapping the module attribute is picked up."""
    import src.database as database
    saved = database.SessionLocal
    database.SessionLocal = factory
    try:
        yield
    finally:
        database.SessionLocal = saved


def test_api_epistemic_trend_serializes_timestamps_and_shape():
    from fastapi.testclient import TestClient
    from src.web.app import app as real_app

    Session = _make_session_factory()
    db = Session()
    try:
        _seed_count_dataset(db)
    finally:
        db.close()

    # No-startup TestClient (no context manager -> startup lifespan never runs).
    client = TestClient(real_app, raise_server_exceptions=False)
    with _env(DEV_AUTH_BYPASS="1", REPLIT_DEPLOYMENT=None), _patched_session_factory(Session):
        r = client.get("/api/governance/epistemic-trend?window=10")
    assert r.status_code == 200, (r.status_code, r.text)
    body = r.json()
    # Full shape.
    expected_keys = {
        "points", "totals", "grand_total", "verified_ratio", "unknown_ratio",
        "run_count", "window", "window_label", "windows",
    }
    assert expected_keys <= set(body.keys()), body.keys()
    assert body["window"] == "10", body["window"]
    assert body["run_count"] == 10, body["run_count"]
    assert body["windows"] and body["windows"][0]["key"] == EPISTEMIC_WINDOWS[0]["key"]
    # Timestamps serialized to ISO strings (datetimes are not JSON-native).
    assert body["points"], "expected windowed points"
    for p in body["points"]:
        assert isinstance(p["created_at"], str), p
        # Parseable ISO-8601 round-trip.
        datetime.fromisoformat(p["created_at"])


def test_api_epistemic_trend_unknown_window_falls_back():
    from fastapi.testclient import TestClient
    from src.web.app import app as real_app

    Session = _make_session_factory()
    db = Session()
    try:
        _seed_count_dataset(db)
    finally:
        db.close()

    client = TestClient(real_app, raise_server_exceptions=False)
    with _env(DEV_AUTH_BYPASS="1", REPLIT_DEPLOYMENT=None), _patched_session_factory(Session):
        r = client.get("/api/governance/epistemic-trend?window=bogus")
    assert r.status_code == 200, (r.status_code, r.text)
    assert r.json()["window"] == DEFAULT_EPISTEMIC_WINDOW, r.json()["window"]


def test_api_epistemic_trend_requires_auth():
    from fastapi.testclient import TestClient
    from src.web.app import app as real_app

    client = TestClient(real_app, raise_server_exceptions=False)
    with _env(DEV_AUTH_BYPASS=None, REPLIT_DEPLOYMENT=None):
        r = client.get("/api/governance/epistemic-trend", follow_redirects=False)
    # Anonymous -> require_auth raises 303 -> /login (never serves data).
    assert r.status_code in (303, 401), r.status_code


# --------------------------------------------------------------------------- #
# Standalone runner (mirrors the other suites in tests/)
# --------------------------------------------------------------------------- #

def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("ok   %s" % fn.__name__)
        except Exception as e:
            failed += 1
            print("FAIL %s: %s: %s" % (fn.__name__, type(e).__name__, e))
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
