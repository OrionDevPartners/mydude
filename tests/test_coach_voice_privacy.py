"""Regression tests for the voice/audio mood-capture privacy rules.

The voice path (Hume *prosody*) must honor the exact same governance rules as the
typed-text path. These were verified by hand but had no committed regression
test, so they could silently regress. This suite locks them in, fully offline
(no real Hume, no network, no database):

  1. ``ingest_audio`` (``src/coach/ingestion.py``) fail-loud guards:
       * strict-private mode REFUSES the Hume cloud audio path (raises) — there
         is no on-device prosody model, so the recording must never egress.
       * when Hume is unconfigured it raises ``CoachNotConfigured`` (no faked
         signal).
       * empty audio raises ``ValueError`` before any provider call.
     In every failure case NO ``MoodSignal`` is written (no half-captured row).

  2. A successful prosody result writes a ``MoodSignal`` with ``valence=None``
     (prosody emits no sentiment scale, so a derived valence would be fabricated
     data — governance pillar #1) AND a LOCAL-ONLY memory node (the sensitive
     emotional data never reaches the cloud store). The Hume client's
     ``analyze_audio`` is mocked.

  3. ``HumeClient._normalize`` returns ``valence=None`` for a prosody payload
     (the source of the "no fabricated sentiment" guarantee).

  4. The ``/api/coach/ingest-audio`` endpoint (``src/web/api/router.py``) maps
     each error to the SAME HTTP status code as the text ``/api/coach/ingest``
     endpoint (CoachNotConfigured/ValueError -> 400; auth/provider -> 502) and
     rejects an empty upload with 400.

Runnable two ways:
  * ``python tests/test_coach_voice_privacy.py``  (standalone; exits non-zero on failure)
  * ``pytest tests/test_coach_voice_privacy.py``   (test_* functions; no plugins needed)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models import MoodSignal, CoachAuditLog
from src.coach import ingestion as ing_mod
from src.coach import providers as prov_mod
from src.coach.providers import (
    CoachNotConfigured,
    CoachAuthError,
    CoachProviderError,
)
import src.memory.substrate as substrate_mod


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

class _FakeDB:
    """Minimal SQLAlchemy session double.

    Assigns an autoincrement id to every row it stores (so ``_audit``'s
    ``"#%d"`` formatting works) and records which rows were added so a test can
    prove whether a MoodSignal was written.
    """

    def __init__(self):
        self.added = []
        self.audits = []
        self._next_id = 1

    def add(self, row):
        self.added.append(row)
        if isinstance(row, CoachAuditLog):
            self.audits.append(row)
        if getattr(row, "id", None) is None:
            try:
                row.id = self._next_id
                self._next_id += 1
            except (AttributeError, TypeError):
                pass

    def commit(self):
        return None

    def refresh(self, row):
        return None

    def close(self):
        return None


class _FakeEntry:
    def __init__(self, memory_id):
        self.memory_id = memory_id


class _FakeSubstrate:
    """Captures every ``write_claim`` so a test can prove the node was written
    LOCAL-ONLY (``local_only=True``)."""

    def __init__(self):
        self.writes = []

    def write_claim(self, content, category="fact", confidence=1.0, source="",
                    verified=False, metadata=None, local_only=False):
        self.writes.append({
            "content": content,
            "category": category,
            "metadata": metadata,
            "local_only": local_only,
        })
        return _FakeEntry("mem-%d" % len(self.writes))


# Normalized prosody result, exactly the shape HumeClient.analyze_audio returns:
# emotions ranked, a label, NO valence (prosody emits no sentiment scale).
_PROSODY_RESULT = {
    "provider": "hume",
    "model": "prosody",
    "emotions": [{"name": "Calmness", "score": 0.42},
                 {"name": "Joy", "score": 0.31}],
    "label": "Calmness",
    "score": 0.42,
    "valence": None,
    "arousal": None,
    "sentiment_mean": None,
    "tokens_analyzed": 3,
    "modality": "voice",
}


class _FakeHumeProvider:
    """Stands in for ``HumeClient`` with a mocked ``analyze_audio``."""

    provider = "hume"

    def __init__(self, result=None, exc=None):
        self._result = result if result is not None else dict(_PROSODY_RESULT)
        self._exc = exc
        self.calls = []

    def analyze_audio(self, audio_bytes, filename="recording.webm"):
        self.calls.append((audio_bytes, filename))
        if self._exc is not None:
            raise self._exc
        return self._result


def _patch_provider(provider=None, get_exc=None):
    """Patch ``providers.get_mood_provider`` (imported inside ingest_audio).

    Returns (restore, provider). If ``get_exc`` is set, get_mood_provider itself
    raises it (simulates an unconfigured provider)."""
    saved = prov_mod.get_mood_provider

    def _get():
        if get_exc is not None:
            raise get_exc
        return provider

    prov_mod.get_mood_provider = _get
    return (lambda: setattr(prov_mod, "get_mood_provider", saved)), provider


def _patch_substrate(sub):
    saved = substrate_mod.get_substrate
    substrate_mod.get_substrate = lambda: sub
    return lambda: setattr(substrate_mod, "get_substrate", saved)


def _mood_rows(db):
    return [r for r in db.added if isinstance(r, MoodSignal)]


# --------------------------------------------------------------------------- #
# 1. ingest_audio fail-loud guards
# --------------------------------------------------------------------------- #

def test_ingest_audio_refuses_cloud_in_strict_mode():
    db = _FakeDB()
    restore, _ = _patch_provider(_FakeHumeProvider())
    raised = False
    try:
        ing_mod.ingest_audio(db, b"voice-bytes", filename="r.webm",
                             strict_private=True)
    except ValueError as e:
        raised = "strict-private" in str(e).lower()
    finally:
        restore()
    assert raised, "strict-private must refuse the Hume cloud voice path (fail loud)"
    assert _mood_rows(db) == [], "no signal may be written when ingestion refuses"


def test_ingest_audio_fails_loud_when_hume_unconfigured():
    db = _FakeDB()
    restore, _ = _patch_provider(get_exc=CoachNotConfigured("Hume is not connected."))
    raised = False
    try:
        ing_mod.ingest_audio(db, b"voice-bytes", strict_private=False)
    except CoachNotConfigured:
        raised = True
    finally:
        restore()
    assert raised, "must raise CoachNotConfigured when Hume is unconfigured"
    assert _mood_rows(db) == [], "no signal may be written when the provider is missing"


def test_ingest_audio_rejects_empty_audio():
    db = _FakeDB()
    raised = False
    try:
        ing_mod.ingest_audio(db, b"", strict_private=False)
    except ValueError:
        raised = True
    assert raised, "empty audio must be rejected (fail loud)"
    assert _mood_rows(db) == [], "no signal may be written for empty audio"


def test_ingest_audio_rejects_provider_without_audio_support():
    db = _FakeDB()

    class _TextOnly:
        provider = "hume"

        def analyze_text(self, text):
            return {}

    restore, _ = _patch_provider(_TextOnly())
    raised = False
    try:
        ing_mod.ingest_audio(db, b"voice-bytes", strict_private=False)
    except ValueError as e:
        raised = "audio" in str(e).lower()
    finally:
        restore()
    assert raised, "a provider without analyze_audio must fail loud"
    assert _mood_rows(db) == [], "no signal may be written when audio is unsupported"


# --------------------------------------------------------------------------- #
# 2. successful prosody result: valence=None + LOCAL-ONLY node
# --------------------------------------------------------------------------- #

def test_ingest_audio_success_writes_valence_none_and_local_only_node():
    db = _FakeDB()
    provider = _FakeHumeProvider()
    sub = _FakeSubstrate()
    restore_p, _ = _patch_provider(provider)
    restore_s = _patch_substrate(sub)
    try:
        res = ing_mod.ingest_audio(db, b"voice-bytes", filename="clip.webm",
                                   strict_private=False)
    finally:
        restore_p()
        restore_s()

    assert provider.calls, "the provider's analyze_audio must be invoked"

    rows = _mood_rows(db)
    assert len(rows) == 1, "exactly one MoodSignal must be written"
    sig = rows[0]
    assert sig.signal_type == "emotion", sig.signal_type
    assert sig.source == "hume_voice", sig.source
    assert sig.valence is None, "prosody has no sentiment scale -> valence must stay None"
    assert sig.label == "Calmness", sig.label

    # Serialized result handed back to the caller mirrors the no-fabrication rule.
    assert res["valence"] is None, "serialized signal must report valence=None"

    # The sensitive emotional node must be written LOCAL-ONLY (never egresses).
    assert len(sub.writes) == 1, "a memory node must be written for the signal"
    assert sub.writes[0]["local_only"] is True, \
        "the voice mood node must be LOCAL-ONLY (private emotional data)"
    assert sub.writes[0]["metadata"].get("valence") is None, \
        "the node metadata must not carry a fabricated valence"

    # Success must be audited as a voice signal (no faked text/sentiment label).
    assert any(a.action == "ingest_voice" for a in db.audits), \
        "a successful voice capture must be audited as ingest_voice"


# --------------------------------------------------------------------------- #
# 3. HumeClient._normalize: prosody yields valence=None at the source
# --------------------------------------------------------------------------- #

def test_normalize_prosody_reports_valence_none():
    from src.coach.client_hume import HumeClient

    client = HumeClient.__new__(HumeClient)  # skip __init__ (no creds needed)
    # A prosody predictions payload: emotions present, NO sentiment block.
    preds = [{
        "results": {"predictions": [{
            "models": {"prosody": {"grouped_predictions": [{
                "predictions": [{
                    "emotions": [
                        {"name": "Calmness", "score": 0.6},
                        {"name": "Joy", "score": 0.3},
                    ],
                }],
            }]}},
        }]},
    }]
    out = client._normalize(preds, "prosody")
    assert out["valence"] is None, "prosody must not fabricate a valence"
    assert out["arousal"] is None, "Hume emits no arousal scalar"
    assert out["label"] == "Calmness", out["label"]
    assert out["sentiment_mean"] is None, "no sentiment block -> no sentiment_mean"


# --------------------------------------------------------------------------- #
# 4. /api/coach/ingest-audio error mapping == /api/coach/ingest (text)
# --------------------------------------------------------------------------- #

def _build_client():
    """TestClient over the real app, NOT entered as a context manager so the
    startup lifespan (DB init / SPA build / scheduler) never runs."""
    from fastapi.testclient import TestClient
    from src.web.app import app as real_app
    from src.web import auth

    client = TestClient(real_app, raise_server_exceptions=False)
    client.cookies.set("session_token",
                       auth._serializer.dumps({"authenticated": True}))
    return client


class _FakeSession:
    def close(self):
        return None


def _patch_sessionlocal():
    import src.database as db_mod
    saved = db_mod.SessionLocal
    db_mod.SessionLocal = lambda: _FakeSession()
    return lambda: setattr(db_mod, "SessionLocal", saved)


def _patch_ingest(text_fn=None, audio_fn=None):
    saved_t = ing_mod.ingest_text
    saved_a = ing_mod.ingest_audio
    if text_fn is not None:
        ing_mod.ingest_text = text_fn
    if audio_fn is not None:
        ing_mod.ingest_audio = audio_fn

    def restore():
        ing_mod.ingest_text = saved_t
        ing_mod.ingest_audio = saved_a

    return restore


def _post_audio(client, data=b"voice-bytes"):
    return client.post(
        "/api/coach/ingest-audio",
        files={"file": ("clip.webm", data, "audio/webm")},
        data={"project_id": "", "event_ref": ""},
        headers={"X-Forwarded-For": "203.0.113.50"},
    )


def _post_text(client):
    return client.post(
        "/api/coach/ingest",
        data={"text": "I feel good today", "prefer": "auto",
              "project_id": "", "event_ref": ""},
        headers={"X-Forwarded-For": "203.0.113.51"},
    )


def test_endpoint_empty_audio_returns_400():
    with _env(DEV_AUTH_BYPASS="1", REPLIT_DEPLOYMENT=None):
        client = _build_client()
        restore_db = _patch_sessionlocal()
        try:
            r = _post_audio(client, data=b"")
        finally:
            restore_db()
    assert r.status_code == 400, (r.status_code, r.text)


def test_endpoint_audio_matches_text_status_for_each_error():
    # Each provider error must map to the SAME status on the voice endpoint as on
    # the text endpoint.
    cases = [
        ("not_configured", lambda: CoachNotConfigured("Hume is not connected."), 400),
        ("value_error", lambda: ValueError("Strict-private mode is on."), 400),
        ("auth_error", lambda: CoachAuthError("Hume rejected the key."), 502),
        ("provider_error", lambda: CoachProviderError("Hume API error."), 502),
    ]
    for name, make_exc, expected in cases:
        with _env(DEV_AUTH_BYPASS="1", REPLIT_DEPLOYMENT=None):
            client = _build_client()
            restore_db = _patch_sessionlocal()

            def _raise_text(*a, **k):
                raise make_exc()

            def _raise_audio(*a, **k):
                raise make_exc()

            restore_ing = _patch_ingest(text_fn=_raise_text, audio_fn=_raise_audio)
            try:
                ra = _post_audio(client)
                rt = _post_text(client)
            finally:
                restore_ing()
                restore_db()
        assert ra.status_code == expected, (name, "audio", ra.status_code, ra.text)
        assert rt.status_code == expected, (name, "text", rt.status_code, rt.text)
        assert ra.status_code == rt.status_code, \
            (name, "audio/text status mismatch", ra.status_code, rt.status_code)


def test_endpoint_audio_success_returns_signal():
    with _env(DEV_AUTH_BYPASS="1", REPLIT_DEPLOYMENT=None):
        client = _build_client()
        restore_db = _patch_sessionlocal()
        serialized = dict(_PROSODY_RESULT)
        serialized.update({"id": 7, "signal_type": "emotion",
                           "source": "hume_voice"})
        restore_ing = _patch_ingest(audio_fn=lambda *a, **k: serialized)
        try:
            r = _post_audio(client)
        finally:
            restore_ing()
            restore_db()
    assert r.status_code == 200, (r.status_code, r.text)
    body = r.json()
    assert body.get("ok") is True, body
    assert body["signal"]["valence"] is None, \
        "the voice signal returned to the UI must report valence=None"


# --------------------------------------------------------------------------- #
# env helper (mirrors tests/test_security_hardening.py)
# --------------------------------------------------------------------------- #

from contextlib import contextmanager


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


# --------------------------------------------------------------------------- #
# standalone runner
# --------------------------------------------------------------------------- #

def _run_all():
    tests = [
        test_ingest_audio_refuses_cloud_in_strict_mode,
        test_ingest_audio_fails_loud_when_hume_unconfigured,
        test_ingest_audio_rejects_empty_audio,
        test_ingest_audio_rejects_provider_without_audio_support,
        test_ingest_audio_success_writes_valence_none_and_local_only_node,
        test_normalize_prosody_reports_valence_none,
        test_endpoint_empty_audio_returns_400,
        test_endpoint_audio_matches_text_status_for_each_error,
        test_endpoint_audio_success_returns_signal,
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
