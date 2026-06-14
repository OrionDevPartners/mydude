"""Tests for the avatar sub-stack governance guarantees.

These run offline, with no real providers, network, or database:

  1. Honest status (``src/avatar/providers.py`` / ``src/avatar/bridge.py``):
       * ``voice_status`` / ``avatar_status`` NEVER raise and report
         not-connected truthfully when nothing is configured.
  2. Fail-loud session start (``src/avatar/sessions.py``):
       * with NEITHER voice nor avatar configured, ``start_session`` FAILS LOUD
         (AvatarNotConfigured) and writes no active session.
  3. Disclosure + consent gate (mandatory in call flows):
       * a consent-required profile starts ``pending_consent`` and does NOT go
         active until consent is granted.
       * consent on a non-pending session is refused (gate) and audited.
       * denied consent moves the session to ``denied`` and never activates.
       * the compliance gate (``ensure_call_compliance``) fails loud without
         disclosure/consent.
  4. Voice-only fallback (no fabrication):
       * voice configured + avatar NOT -> session is created honestly as
         ``voice_only`` with no connection info.
  5. Bridge negotiation (real client, fail loud):
       * a configured avatar backend that ERRORS -> ``needs_provider``, consent is
         NOT rolled back, and the connection token never reaches the audit log.
       * ``bridge.create_session`` fails loud when unconfigured (no placeholder).
       * a plaintext (non-HTTPS) bridge URL is refused, never negotiated, so the
         bridge token is never sent in the clear.
  6. Profile validation: unique name, known providers, JSON-only avatar_config.

The DB, providers, and bridge are all faked, so the suite is hermetic.

Runnable two ways:
  * ``python tests/test_avatar_governance.py``  (standalone, exits non-zero on failure)
  * ``pytest tests/test_avatar_governance.py``   (test_* functions; no plugins needed)
"""
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models import AvatarProfile, AvatarSession, AvatarAuditLog
from src.avatar import sessions as sess
from src.avatar import profiles as profiles_mod
from src.avatar import bridge as bridge_mod
from src.avatar import compliance as comp
from src.avatar import providers as prov


# -- fake DB (supports profile + session query chains, add/commit/delete) -----

class _FakeDB:
    def __init__(self, profile=None):
        self.profile = profile
        self.sessions = {}
        self.audits = []
        self.added = []
        self.deleted = []
        self._next_id = 1
        self._model = None
        self._filter_id = None
        self.commit_count = 0
        self._for_update = False
        self._lock_commit_snapshot = None

    def query(self, model, *a, **k):
        self._model = model
        self._filter_id = None
        self._for_update = False
        return self

    def filter(self, *crit, **k):
        for c in crit:
            try:
                self._filter_id = c.right.value
            except Exception:  # noqa: BLE001 — name filters etc.
                pass
        return self

    def with_for_update(self, *a, **k):
        self._for_update = True
        return self

    def order_by(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def first(self):
        if self._model is AvatarSession:
            row = (self.sessions.get(self._filter_id) if self._filter_id is not None
                   else next(iter(self.sessions.values()), None))
            if self._for_update:
                # Snapshot the commit count at lock-acquisition so a test can
                # assert nothing commits (releasing the lock) before activation.
                self._lock_commit_snapshot = self.commit_count
            return row
        return self.profile

    def all(self):
        if self._model is AvatarSession:
            return sorted(self.sessions.values(), key=lambda s: s.id, reverse=True)
        return [self.profile] if self.profile else []

    def add(self, row):
        self.added.append(row)
        if isinstance(row, AvatarAuditLog):
            self.audits.append(row)
        elif isinstance(row, AvatarSession) and getattr(row, "id", None) is None:
            row.id = self._next_id
            self._next_id += 1
            self.sessions[row.id] = row
        elif isinstance(row, AvatarProfile) and getattr(row, "id", None) is None:
            row.id = self._next_id
            self._next_id += 1
            self.profile = row

    def refresh(self, row):
        return None

    def commit(self):
        self.commit_count += 1
        return None

    def delete(self, row):
        self.deleted.append(row)
        if row is self.profile:
            self.profile = None


def _mk_profile(consent=True, disclosure=True, avatar_provider=None, active=True,
                voice_id="v1"):
    p = AvatarProfile(
        name="Test Bot", persona="friendly", voice_provider="elevenlabs",
        voice_id=voice_id, avatar_provider=avatar_provider,
        disclosure_required=disclosure, consent_required=consent, active=active,
    )
    p.id = 1
    return p


def _patch_provider_checks(voice_ok, avatar_ok):
    """Patch the seams sessions uses to detect configured providers."""
    saved = (sess._voice_ok, sess._avatar_ok)
    sess._voice_ok = lambda: voice_ok
    sess._avatar_ok = lambda provider=None: avatar_ok

    def restore():
        sess._voice_ok, sess._avatar_ok = saved
    return restore


def _audit_actions(db):
    return [a.action for a in db.audits]


def _audit_blob(db):
    return " ".join((a.detail or "") for a in db.audits)


# -- honest status ------------------------------------------------------------

def test_status_functions_never_raise_and_are_honest():
    vs = prov.voice_status()
    assert vs["connected"] in (True, False)
    assert "provider" in vs
    av = bridge_mod.avatar_status()
    assert av["configured"] in (True, False)
    assert "heygen" in av["providers"] and "azure" in av["providers"]


# -- fail-loud session start --------------------------------------------------

def test_start_fails_loud_when_nothing_configured():
    db = _FakeDB(_mk_profile())
    restore = _patch_provider_checks(voice_ok=False, avatar_ok=False)
    raised = False
    try:
        sess.start_session(db, 1)
    except prov.AvatarNotConfigured:
        raised = True
    finally:
        restore()
    assert raised, "must fail loud when no voice/avatar provider exists"
    assert not any(isinstance(r, AvatarSession) for r in db.added), \
        "no session may be created when start fails loud"
    assert "session_not_configured" in _audit_actions(db)


# -- disclosure + consent gate ------------------------------------------------

def test_consent_required_starts_pending_and_not_active():
    db = _FakeDB(_mk_profile(consent=True))
    restore = _patch_provider_checks(voice_ok=True, avatar_ok=False)
    try:
        res = sess.start_session(db, 1)
    finally:
        restore()
    assert res["session"]["status"] == "pending_consent", res
    assert res["session"]["consent_status"] == "pending"
    assert res["session"]["mode"] is None, "must not activate before consent"
    assert res["disclosure"], "disclosure text must be returned"
    assert res["consent_prompt"], "consent prompt must be returned"
    assert "session_requested" in _audit_actions(db)


def test_consent_grant_activates_voice_only_when_no_avatar():
    db = _FakeDB(_mk_profile(consent=True))
    restore = _patch_provider_checks(voice_ok=True, avatar_ok=False)
    try:
        started = sess.start_session(db, 1)
        sid = started["session"]["id"]
        res = sess.record_consent(db, sid, True)
    finally:
        restore()
    assert res["status"] == "active", res
    assert res["mode"] == "voice_only", res
    assert res.get("connection") in (None, {}), "voice-only must carry no connection"
    assert "session_consent_granted" in _audit_actions(db)
    assert "session_voice_only" in _audit_actions(db)


def test_consent_denied_does_not_activate():
    db = _FakeDB(_mk_profile(consent=True))
    restore = _patch_provider_checks(voice_ok=True, avatar_ok=False)
    try:
        started = sess.start_session(db, 1)
        sid = started["session"]["id"]
        res = sess.record_consent(db, sid, False, detail="caller declined")
    finally:
        restore()
    assert res["status"] == "denied", res
    assert res["consent_status"] == "denied"
    assert "session_consent_denied" in _audit_actions(db)
    assert "session_voice_only" not in _audit_actions(db)


def test_double_consent_is_refused():
    db = _FakeDB(_mk_profile(consent=True))
    restore = _patch_provider_checks(voice_ok=True, avatar_ok=False)
    try:
        started = sess.start_session(db, 1)
        sid = started["session"]["id"]
        sess.record_consent(db, sid, True)  # now active
        raised = False
        try:
            sess.record_consent(db, sid, True)  # second consent must be refused
        except PermissionError:
            raised = True
    finally:
        restore()
    assert raised, "a non-pending session must not accept consent again"
    assert "session_consent_blocked" in _audit_actions(db)


def test_compliance_gate_fails_loud():
    profile = _mk_profile(consent=True, disclosure=True)
    s = AvatarSession(disclosure_shown=False, consent_status="pending")
    raised = False
    try:
        comp.ensure_call_compliance(profile, s)
    except comp.DisclosureRequired:
        raised = True
    assert raised, "missing disclosure must fail loud"
    s.disclosure_shown = True
    raised = False
    try:
        comp.ensure_call_compliance(profile, s)
    except comp.ConsentRequired:
        raised = True
    assert raised, "missing consent must fail loud"


# -- bridge negotiation (real client, fail loud) ------------------------------

def test_bridge_success_activates_video_and_never_audits_token():
    db = _FakeDB(_mk_profile(consent=True, avatar_provider="heygen"))
    restore = _patch_provider_checks(voice_ok=True, avatar_ok=True)
    saved = bridge_mod.create_session
    secret_token = "TOKEN-DO-NOT-LOG-12345"

    def _fake_create(provider, **kw):
        return {"provider": "heygen", "transport": "livekit",
                "connection": {"access_token": secret_token, "url": "wss://x"},
                "detail": "negotiated"}

    bridge_mod.create_session = _fake_create
    try:
        started = sess.start_session(db, 1)
        sid = started["session"]["id"]
        res = sess.record_consent(db, sid, True)
    finally:
        bridge_mod.create_session = saved
        restore()
    assert res["status"] == "active" and res["mode"] == "avatar_video", res
    assert res["connection"]["access_token"] == secret_token, "browser gets the token"
    assert secret_token not in _audit_blob(db), \
        "connection tokens must never appear in the audit log"
    assert "session_active" in _audit_actions(db)


def test_active_session_never_persists_room_token_to_db():
    """The browser gets the full descriptor in the RESPONSE, but the room token
    must never be written to the DB (pillar #3 / criterion 4). Only non-secret
    routing the server reuses later (the HeyGen session_id) is persisted."""
    db = _FakeDB(_mk_profile(consent=True, avatar_provider="heygen"))
    restore = _patch_provider_checks(voice_ok=True, avatar_ok=True)
    saved = bridge_mod.create_session
    secret = "ROOM-TOKEN-MUST-NOT-PERSIST-77"

    def _fake_create(provider, **kw):
        return {"provider": "heygen", "transport": "livekit",
                "connection": {"session_id": "sess_x", "url": "wss://x",
                               "access_token": secret},
                "detail": "negotiated"}

    bridge_mod.create_session = _fake_create
    try:
        started = sess.start_session(db, 1)
        sid = started["session"]["id"]
        res = sess.record_consent(db, sid, True)
    finally:
        bridge_mod.create_session = saved
        restore()
    # The browser (THIS response) still receives the full descriptor + token.
    assert res["connection"]["access_token"] == secret, "browser gets the token"
    # The persisted DB row must NOT contain the token...
    stored = db.sessions[sid].connection_json
    assert secret not in (stored or ""), "room token must never be persisted to the DB"
    parsed = json.loads(stored)
    assert parsed == {"session_id": "sess_x"}, parsed
    assert "access_token" not in parsed and "url" not in parsed
    # ...and a LATER read (a fresh request, no in-memory descriptor) never
    # surfaces a token either — start_stream still has the session_id it needs.
    later = sess.get_session(db, sid, include_connection=True)
    assert secret not in json.dumps(later), "a later read must never expose the token"
    assert later["connection"]["session_id"] == "sess_x"


def test_bridge_error_marks_needs_provider_and_keeps_consent():
    db = _FakeDB(_mk_profile(consent=True, avatar_provider="heygen"))
    restore = _patch_provider_checks(voice_ok=True, avatar_ok=True)
    saved = bridge_mod.create_session

    def _boom(provider, **kw):
        raise prov.AvatarProviderError("bridge down")

    bridge_mod.create_session = _boom
    try:
        started = sess.start_session(db, 1)
        sid = started["session"]["id"]
        raised = False
        try:
            sess.record_consent(db, sid, True)
        except prov.AvatarProviderError:
            raised = True
    finally:
        bridge_mod.create_session = saved
        restore()
    assert raised, "a failed bridge negotiation must fail loud"
    s = db.sessions[sid]
    assert s.status == "needs_provider", s.status
    assert s.consent_status == "granted", "consent must NOT be rolled back on bridge failure"
    assert "session_needs_provider" in _audit_actions(db)


def test_bridge_create_session_fails_loud_when_unconfigured():
    # No HEYGEN key and no AVATAR_BRIDGE_URL in the test env -> must fail loud.
    raised = False
    try:
        bridge_mod.create_session("heygen", avatar_config={"avatar_id": "x"})
    except prov.AvatarNotConfigured:
        raised = True
    assert raised, "bridge must fail loud (no placeholder) when unconfigured"


def test_bridge_refuses_non_https_url():
    # A plaintext bridge URL must never be used — the bearer token would leak.
    saved = bridge_mod._bridge_config
    bridge_mod._bridge_config = lambda: ("http://insecure.example.com", "SECRET-TOKEN")
    try:
        assert bridge_mod.avatar_configured("azure") is False, \
            "a plaintext URL must not count as a configured backend"
        st = bridge_mod.avatar_status()
        assert st["providers"]["azure"]["configured"] is False
        assert "https" in st["providers"]["azure"]["detail"].lower(), \
            "status must honestly flag the https requirement"
        raised = False
        try:
            bridge_mod.create_session("azure")
        except prov.AvatarNotConfigured:
            raised = True
        assert raised, "a plaintext bridge URL must fail loud, never negotiate"
    finally:
        bridge_mod._bridge_config = saved


# -- live stream-start (media publish) ----------------------------------------

def _add_active_session(db, mode="avatar_video", provider="heygen", connection=None):
    s = AvatarSession(
        avatar_profile_id=1, status="active", mode=mode, provider=provider,
        consent_status="granted",
        connection_json=json.dumps(connection) if connection is not None else None,
    )
    db.add(s)
    return s


def test_stream_start_publishes_media_and_never_returns_or_audits_token():
    db = _FakeDB(_mk_profile(consent=True, avatar_provider="heygen"))
    secret = "LK-TOKEN-DO-NOT-LEAK-99"
    s = _add_active_session(db, connection={
        "session_id": "sess_x", "access_token": secret, "url": "wss://x"})
    saved = bridge_mod.start_stream
    captured = {}

    def _fake_start(provider, connection):
        captured["provider"] = provider
        captured["connection"] = connection
        return True

    bridge_mod.start_stream = _fake_start
    try:
        res = sess.start_stream(db, s.id)
    finally:
        bridge_mod.start_stream = saved
    assert res == {"id": s.id, "status": "active", "mode": "avatar_video",
                   "provider": "heygen"}, res
    assert "connection" not in res and secret not in json.dumps(res), \
        "sanitized status must never echo the connection token"
    assert captured["provider"] == "heygen"
    assert captured["connection"]["session_id"] == "sess_x", \
        "the bridge gets the stored session_id to publish media"
    assert secret not in _audit_blob(db), "tokens must never reach the audit log"
    assert "session_stream_started" in _audit_actions(db)


def test_stream_start_refused_on_voice_only_and_bridge_not_called():
    db = _FakeDB(_mk_profile(consent=True))
    s = _add_active_session(db, mode="voice_only", provider=None, connection=None)
    saved = bridge_mod.start_stream
    called = {"n": 0}

    def _fake_start(provider, connection):
        called["n"] += 1
        return True

    bridge_mod.start_stream = _fake_start
    raised = False
    try:
        sess.start_stream(db, s.id)
    except PermissionError:
        raised = True
    finally:
        bridge_mod.start_stream = saved
    assert raised, "stream-start on a non-video session must fail loud"
    assert called["n"] == 0, "the provider must not be told to publish media"
    assert "session_stream_blocked" in _audit_actions(db)
    assert "session_stream_started" not in _audit_actions(db)


def test_heygen_start_stream_posts_only_session_id_with_server_key():
    saved_key = bridge_mod._heygen_key
    real_httpx = bridge_mod.httpx
    bridge_mod._heygen_key = lambda: ("SERVER-KEY", "vault")
    captured = {}

    class _Resp:
        status_code = 200
        text = "{}"

    def _fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _Resp()

    bridge_mod.httpx = types.SimpleNamespace(post=_fake_post,
                                             HTTPError=real_httpx.HTTPError)
    try:
        ok = bridge_mod._heygen_start_stream(
            {"session_id": "sess_x", "access_token": "BROWSER-TOKEN-LEAK"})
    finally:
        bridge_mod._heygen_key = saved_key
        bridge_mod.httpx = real_httpx
    assert ok is True
    assert captured["url"].endswith("/v1/streaming.start")
    assert captured["json"] == {"session_id": "sess_x"}, captured["json"]
    assert "BROWSER-TOKEN-LEAK" not in json.dumps(captured["json"]), \
        "the browser's room token must never be re-sent to the provider"
    assert captured["headers"]["x-api-key"] == "SERVER-KEY", \
        "streaming.start must authenticate with the server-side key"


def test_heygen_start_stream_fails_loud_without_session_id():
    saved_key = bridge_mod._heygen_key
    bridge_mod._heygen_key = lambda: ("SERVER-KEY", "vault")
    raised = False
    try:
        bridge_mod._heygen_start_stream({})
    except prov.AvatarProviderError:
        raised = True
    finally:
        bridge_mod._heygen_key = saved_key
    assert raised, "no session_id must fail loud, never a placeholder start"


def test_bridge_start_stream_slug_is_noop_and_unknown_fails_loud():
    assert bridge_mod.start_stream("azure", {}) is True, \
        "external bridge publishes on WebRTC connect — start_stream is a no-op"
    raised = False
    try:
        bridge_mod.start_stream("bogus", {})
    except prov.AvatarNotConfigured:
        raised = True
    assert raised, "an unknown provider must fail loud"


# -- profile validation -------------------------------------------------------

def test_profile_create_rejects_duplicate_name():
    db = _FakeDB(_mk_profile())  # an existing 'Test Bot'
    raised = False
    try:
        profiles_mod.create_profile(db, "Test Bot")
    except ValueError:
        raised = True
    assert raised, "duplicate profile names must be rejected"


def test_profile_create_rejects_bad_config_and_unknown_provider():
    db = _FakeDB()
    raised = False
    try:
        profiles_mod.create_profile(db, "Bot A", avatar_config="{not json}")
    except ValueError:
        raised = True
    assert raised, "invalid avatar_config JSON must be rejected"
    db2 = _FakeDB()
    raised = False
    try:
        profiles_mod.create_profile(db2, "Bot B", avatar_provider="bogus")
    except ValueError:
        raised = True
    assert raised, "unknown avatar provider must be rejected"


def test_profile_create_ok():
    db = _FakeDB()
    res = profiles_mod.create_profile(
        db, "Sales Bot", persona="warm", voice_id="abc",
        avatar_provider="heygen", avatar_config={"avatar_id": "a1"})
    assert res["name"] == "Sales Bot"
    assert res["avatar_provider"] == "heygen"
    assert res["avatar_config"] == {"avatar_id": "a1"}
    assert res["disclosure_required"] is True and res["consent_required"] is True
    assert "profile_created" in _audit_actions(db)


def test_retry_activates_when_backend_recovers():
    """A ``needs_provider`` session whose consent was already granted can be
    retried in place once the backend recovers — without re-collecting consent,
    and without ever persisting/auditing the room token (pillar #3)."""
    db = _FakeDB(_mk_profile(consent=True, avatar_provider="heygen"))
    restore = _patch_provider_checks(voice_ok=True, avatar_ok=True)
    saved = bridge_mod.create_session
    secret = "RETRY-TOKEN-OK-999"

    def _boom(provider, **kw):
        raise prov.AvatarProviderError("bridge down")

    def _ok(provider, **kw):
        return {"provider": "heygen", "transport": "livekit",
                "connection": {"session_id": "sess_retry", "url": "wss://x",
                               "access_token": secret},
                "detail": "negotiated"}

    try:
        bridge_mod.create_session = _boom
        started = sess.start_session(db, 1)
        sid = started["session"]["id"]
        try:
            sess.record_consent(db, sid, True)
        except prov.AvatarProviderError:
            pass
        assert db.sessions[sid].status == "needs_provider", db.sessions[sid].status
        # Operator fixes the backend -> retry the SAME session in place.
        bridge_mod.create_session = _ok
        res = sess.retry_session(db, sid)
    finally:
        bridge_mod.create_session = saved
        restore()
    assert res["status"] == "active" and res["mode"] == "avatar_video", res
    assert res["connection"]["access_token"] == secret, "browser gets the token on retry"
    assert db.sessions[sid].consent_status == "granted", "consent must never be re-collected"
    # The token is never persisted to the DB nor written to the audit trail.
    assert secret not in (db.sessions[sid].connection_json or ""), "token must not persist"
    assert secret not in _audit_blob(db), "token must never reach the audit log"
    assert "session_retry" in _audit_actions(db)


def test_retry_refused_for_ineligible_sessions():
    """Retry is refused (and audited) for a session that is not
    ``needs_provider`` and for a ``needs_provider`` session without granted
    consent."""
    # (a) not in needs_provider -> refused
    db = _FakeDB(_mk_profile(consent=True, avatar_provider="heygen"))
    active = AvatarSession(avatar_profile_id=1, status="active", mode="avatar_video",
                           consent_status="granted", disclosure_shown=True)
    db.add(active)
    refused = False
    try:
        sess.retry_session(db, active.id)
    except PermissionError:
        refused = True
    assert refused, "retry must be refused when the session is not needs_provider"
    assert "session_retry_blocked" in _audit_actions(db)

    # (b) needs_provider but consent not granted -> refused
    db2 = _FakeDB(_mk_profile(consent=True, avatar_provider="heygen"))
    pending = AvatarSession(avatar_profile_id=1, status="needs_provider",
                            consent_status="pending", disclosure_shown=True)
    db2.add(pending)
    refused2 = False
    try:
        sess.retry_session(db2, pending.id)
    except PermissionError:
        refused2 = True
    assert refused2, "retry must be refused without granted consent"
    assert "session_retry_blocked" in _audit_actions(db2)


def test_retry_holds_lock_through_activation():
    """The FOR UPDATE lock taken for validation must still be held when
    activation runs: retry_session must NOT commit between the locked read and
    _activate, or two concurrent retries could both pass and double-activate."""
    db = _FakeDB(_mk_profile(consent=True, avatar_provider="heygen"))
    s = AvatarSession(avatar_profile_id=1, status="needs_provider",
                      consent_status="granted", disclosure_shown=True)
    db.add(s)
    saved_activate = sess._activate
    seen = {}

    def _spy_activate(_db, session, profile):
        seen["entry_commits"] = _db.commit_count
        session.status = "active"
        session.mode = "avatar_video"
        _db.commit()
        return {"access_token": "tok"}

    try:
        sess._activate = _spy_activate
        sess.retry_session(db, s.id)
    finally:
        sess._activate = saved_activate
    assert seen["entry_commits"] == db._lock_commit_snapshot, (
        "retry_session committed between the FOR UPDATE read and _activate, "
        "releasing the row lock before activation")


def _run_all():
    tests = [
        test_status_functions_never_raise_and_are_honest,
        test_start_fails_loud_when_nothing_configured,
        test_consent_required_starts_pending_and_not_active,
        test_consent_grant_activates_voice_only_when_no_avatar,
        test_consent_denied_does_not_activate,
        test_double_consent_is_refused,
        test_compliance_gate_fails_loud,
        test_bridge_success_activates_video_and_never_audits_token,
        test_active_session_never_persists_room_token_to_db,
        test_bridge_error_marks_needs_provider_and_keeps_consent,
        test_bridge_create_session_fails_loud_when_unconfigured,
        test_bridge_refuses_non_https_url,
        test_stream_start_publishes_media_and_never_returns_or_audits_token,
        test_stream_start_refused_on_voice_only_and_bridge_not_called,
        test_heygen_start_stream_posts_only_session_id_with_server_key,
        test_heygen_start_stream_fails_loud_without_session_id,
        test_bridge_start_stream_slug_is_noop_and_unknown_fails_loud,
        test_profile_create_rejects_duplicate_name,
        test_profile_create_rejects_bad_config_and_unknown_provider,
        test_profile_create_ok,
        test_retry_activates_when_backend_recovers,
        test_retry_refused_for_ineligible_sessions,
        test_retry_holds_lock_through_activation,
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
