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
import os
import sys

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

    def query(self, model, *a, **k):
        self._model = model
        self._filter_id = None
        return self

    def filter(self, *crit, **k):
        for c in crit:
            try:
                self._filter_id = c.right.value
            except Exception:  # noqa: BLE001 — name filters etc.
                pass
        return self

    def with_for_update(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def first(self):
        if self._model is AvatarSession:
            if self._filter_id is not None:
                return self.sessions.get(self._filter_id)
            return next(iter(self.sessions.values()), None)
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
        test_bridge_error_marks_needs_provider_and_keeps_consent,
        test_bridge_create_session_fails_loud_when_unconfigured,
        test_bridge_refuses_non_https_url,
        test_profile_create_rejects_duplicate_name,
        test_profile_create_rejects_bad_config_and_unknown_provider,
        test_profile_create_ok,
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
