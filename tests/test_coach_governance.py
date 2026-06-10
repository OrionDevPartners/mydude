"""Tests for the coach sub-stack governance guarantees.

These run offline, with no real providers, network, or database:

  1. The two-phase secretary gate (``src/coach/secretary.py``):
       * ``request_action`` creates a ``pending_confirm`` row and sends nothing.
       * ``confirm_action`` refuses anything not awaiting confirmation (gate).
       * with no channel provider it marks ``needs_provider`` and FAILS LOUD —
         never a faked "sent".
       * a second confirm after a successful send is refused (no double-send).
       * confirm re-validates the STORED request and will not dispatch a
         tampered/invalid stored payload.
       * ``reject_action`` moves a pending action to ``rejected``.
  2. Private-Mode for inference (``src/coach/sentiment.py`` /
     ``src/coach/ingestion.py``):
       * sentiment threads ``strict_private`` into the governed swarm so raw
         journal text can be pinned to LOCAL providers.
       * in strict-private mode the Hume CLOUD emotion path is REFUSED (fail
         loud) and ``auto`` ingest falls back to local sentiment — raw text never
         egresses off-device.
  3. The life-coach (``src/coach/coach.py``) short-circuits to
     ``insufficient_data`` BEFORE calling any LLM when recall is empty (fail loud
     rather than fabricate a pattern).

The DB, the delivery layer, the memory substrate, and the swarm are all faked, so
the suite is hermetic.

Runnable two ways:
  * ``python tests/test_coach_governance.py``  (standalone, exits non-zero on failure)
  * ``pytest tests/test_coach_governance.py``   (test_* functions; no plugins needed)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models import SecretaryRequest, MoodSignal, CoachAuditLog
from src.coach import secretary as sec
from src.coach import delivery as delivery_mod
from src.coach import sentiment as sent_mod
from src.coach import ingestion as ing_mod
from src.coach import coach as coach_mod
import src.memory.substrate as substrate_mod


# -- fake DB (supports the secretary query chain + add/commit/delete) ---------

class _FakeDB:
    def __init__(self, req=None):
        self.req = req
        self.audits = []
        self.added = []
        self.deleted = []
        self._next_id = 1

    def query(self, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def with_for_update(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def first(self):
        return self.req

    def all(self):
        return [self.req] if self.req else []

    def add(self, row):
        self.added.append(row)
        if isinstance(row, CoachAuditLog):
            self.audits.append(row)
        elif isinstance(row, SecretaryRequest) and getattr(row, "id", None) is None:
            row.id = self._next_id
            self._next_id += 1
            self.req = row

    def refresh(self, row):
        return None

    def commit(self):
        return None

    def delete(self, row):
        self.deleted.append(row)
        if row is self.req:
            self.req = None

    def close(self):
        return None


def _patch_delivery(configured, dispatch_result=None, dispatch_exc=None, calls=None):
    """Patch the delivery seam secretary imports at confirm time."""
    saved = (delivery_mod.channel_configured, delivery_mod.dispatch)

    def _dispatch(channel, **kw):
        if calls is not None:
            calls.append((channel, kw))
        if dispatch_exc is not None:
            raise dispatch_exc
        return dispatch_result or {"provider": "test", "detail": "sent ok"}

    delivery_mod.channel_configured = lambda ch: configured
    delivery_mod.dispatch = _dispatch

    def restore():
        delivery_mod.channel_configured, delivery_mod.dispatch = saved

    return restore


def _audit_actions(db):
    return [a.action for a in db.audits]


# -- two-phase secretary gate -------------------------------------------------

def test_request_creates_pending_and_sends_nothing():
    db = _FakeDB()
    res = sec.request_action(db, "draft_email", recipient="a@b.com",
                             subject="Hi", body="Hello there")
    assert res["status"] == "pending_confirm", res
    assert res["channel"] == "email", res
    assert db.req.status == "pending_confirm"
    assert "action_requested" in _audit_actions(db)


def test_confirm_refuses_when_not_confirmable():
    req = SecretaryRequest(kind="draft_email", channel="email", recipient="a@b.com",
                           body="hi", status="rejected")
    req.id = 7
    db = _FakeDB(req)
    calls = []
    restore = _patch_delivery(True, calls=calls)
    raised = False
    try:
        sec.confirm_action(db, 7)
    except PermissionError:
        raised = True
    finally:
        restore()
    assert raised, "confirm must refuse a non-confirmable action"
    assert req.status == "rejected", "status must be unchanged"
    assert calls == [], "dispatch must never run for a gated action"
    assert "action_confirm_blocked" in _audit_actions(db)


def test_confirm_without_provider_fails_loud_not_sent():
    db = _FakeDB()
    sec.request_action(db, "draft_email", recipient="a@b.com", body="hi")
    calls = []
    restore = _patch_delivery(False, calls=calls)  # no provider configured
    raised = False
    try:
        sec.confirm_action(db, db.req.id)
    except delivery_mod.DeliveryNotConfigured:
        raised = True
    finally:
        restore()
    assert raised, "must fail loud when no provider is configured"
    assert db.req.status == "needs_provider", db.req.status
    assert db.req.status != "sent"
    assert calls == [], "must not dispatch without a provider"
    assert "action_needs_provider" in _audit_actions(db)


def test_double_confirm_blocks_second_send():
    db = _FakeDB()
    sec.request_action(db, "draft_email", recipient="a@b.com", body="hi")
    calls = []
    restore = _patch_delivery(True, dispatch_result={"provider": "resend", "detail": "ok"},
                              calls=calls)
    try:
        first = sec.confirm_action(db, db.req.id)
        assert first["status"] == "sent", first
        # Second confirm on the now-sent row must be refused.
        raised = False
        try:
            sec.confirm_action(db, db.req.id)
        except PermissionError:
            raised = True
        assert raised, "a sent action must not be confirmable again"
    finally:
        restore()
    assert len(calls) == 1, "dispatch must run exactly once (no double-send)"


def test_confirm_revalidates_stored_request():
    db = _FakeDB()
    sec.request_action(db, "draft_email", recipient="a@b.com", body="hi")
    # Tamper with the STORED row so it no longer validates (defense in depth).
    db.req.body = ""
    calls = []
    restore = _patch_delivery(True, calls=calls)
    raised = False
    try:
        sec.confirm_action(db, db.req.id)
    except ValueError:
        raised = True
    finally:
        restore()
    assert raised, "confirm must re-validate the stored request"
    assert db.req.status == "failed", db.req.status
    assert calls == [], "an invalid stored request must never dispatch"


def test_reject_marks_rejected_then_refuses_again():
    db = _FakeDB()
    sec.request_action(db, "draft_text", recipient="+15551234567", body="hey")
    res = sec.reject_action(db, db.req.id)
    assert res["status"] == "rejected", res
    raised = False
    try:
        sec.reject_action(db, db.req.id)
    except PermissionError:
        raised = True
    assert raised, "a rejected action must not be rejectable again"


# -- Private-Mode for inference ----------------------------------------------

def test_sentiment_threads_strict_private():
    captured = {}
    saved = sent_mod.call_team_sync

    def _fake(system, user, roles_hint=None, strict_private=False):
        captured["strict_private"] = strict_private
        return {"merged": '{"valence":0.2,"arousal":0.3,"label":"calm","summary":"ok"}',
                "compliance_scores": {"x": 1.0}, "hallucination_risks": {}}

    sent_mod.call_team_sync = _fake
    try:
        sent_mod.analyze_text_sentiment("I feel good", strict_private=True)
        assert captured["strict_private"] is True
        sent_mod.analyze_text_sentiment("I feel good", strict_private=False)
        assert captured["strict_private"] is False
    finally:
        sent_mod.call_team_sync = saved


def test_ingest_refuses_cloud_emotion_in_strict_mode():
    db = _FakeDB()
    raised = False
    try:
        ing_mod.ingest_text(db, "private journal entry", prefer="emotion",
                            strict_private=True)
    except ValueError as e:
        raised = "strict-private" in str(e).lower()
    assert raised, "strict-private must refuse the Hume cloud emotion path"
    assert not any(isinstance(r, MoodSignal) for r in db.added), \
        "no signal may be written when ingestion fails loud"


def test_ingest_auto_uses_local_sentiment_in_strict_mode():
    db = _FakeDB()
    calls = {"sentiment": 0}
    s_saved = sent_mod.analyze_text_sentiment
    ws_saved = ing_mod._write_signal
    au_saved = ing_mod._audit

    def _fake_sentiment(text, strict_private=None):
        calls["sentiment"] += 1
        assert strict_private is True, "auto+strict must pin sentiment to local"
        return {"valence": 0.1, "arousal": 0.2, "label": "calm", "summary": "ok"}

    def _fake_write_signal(db, **kw):
        sig = MoodSignal(signal_type=kw.get("signal_type"))
        sig.id = 1
        return sig

    sent_mod.analyze_text_sentiment = _fake_sentiment
    ing_mod._write_signal = _fake_write_signal
    ing_mod._audit = lambda *a, **k: None
    try:
        ing_mod.ingest_text(db, "how was today", prefer="auto", strict_private=True)
        assert calls["sentiment"] == 1, "auto+strict must route to local sentiment"
    finally:
        sent_mod.analyze_text_sentiment = s_saved
        ing_mod._write_signal = ws_saved
        ing_mod._audit = au_saved


# -- life-coach fail-loud -----------------------------------------------------

def test_ask_insufficient_data_short_circuits_before_llm():
    db = _FakeDB()
    called = {"llm": 0}
    s_saved = substrate_mod.get_substrate
    l_saved = coach_mod.call_team_sync

    class _EmptySub:
        def recall(self, *a, **k):
            return []

    def _boom(*a, **k):
        called["llm"] += 1
        return {"merged": "should not happen", "compliance_scores": {"x": 1.0}}

    substrate_mod.get_substrate = lambda: _EmptySub()
    coach_mod.call_team_sync = _boom
    try:
        res = coach_mod.ask(db, "How am I trending lately?")
        assert res["status"] == "insufficient_data", res
        assert res["answer"] is None, res
        assert res["citations"] == [], res
        assert called["llm"] == 0, "must not call the LLM with nothing to ground on"
    finally:
        substrate_mod.get_substrate = s_saved
        coach_mod.call_team_sync = l_saved
    assert "ask" in _audit_actions(db)


def _run_all():
    tests = [
        test_request_creates_pending_and_sends_nothing,
        test_confirm_refuses_when_not_confirmable,
        test_confirm_without_provider_fails_loud_not_sent,
        test_double_confirm_blocks_second_send,
        test_confirm_revalidates_stored_request,
        test_reject_marks_rejected_then_refuses_again,
        test_sentiment_threads_strict_private,
        test_ingest_refuses_cloud_emotion_in_strict_mode,
        test_ingest_auto_uses_local_sentiment_in_strict_mode,
        test_ask_insufficient_data_short_circuits_before_llm,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print("PASS", t.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL", t.__name__, "->", e)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("ERROR", t.__name__, "->", type(e).__name__, e)
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
