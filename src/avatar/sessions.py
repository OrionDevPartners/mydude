"""Two-phase, consent-gated avatar session lifecycle.

Mirrors the secretary gate (``src/coach/secretary.py``): a session is created in
``pending_consent`` and only goes ``active`` after the AI-use disclosure has been
shown and recording consent is granted. Consent is persisted BEFORE any bridge
negotiation, so a bridge failure never rolls consent back — it moves the session to
``needs_provider`` — the operator fixes the backend, then RETRIES the session in
place (``retry_session``), reusing the consent already given rather than
re-collecting it. State transitions re-validate the STORED session,
lock the row (``FOR UPDATE``) so two concurrent starts can't double-activate, and
audit every transition (including blocked / needs-provider / voice-only).

Honesty rules (governance pillar #1):
  * If NEITHER voice NOR avatar is configured, ``start_session`` fails loud.
  * If the avatar backend is unavailable but voice is, the session is created
    honestly as ``voice_only`` — connection info is never fabricated.
  * A configured avatar backend that ERRORS during negotiation -> ``needs_provider``
    (fail loud), never a silent downgrade.
"""
import json
import logging
from datetime import datetime

from src.models import AvatarProfile, AvatarSession, AvatarAuditLog
from src.avatar.compliance import (
    ensure_call_compliance, disclosure_text, consent_prompt,
    DisclosureRequired, ConsentRequired,
)
from src.avatar.providers import (
    AvatarNotConfigured, AvatarAuthError, AvatarProviderError,
)

logger = logging.getLogger(__name__)


def _audit(db, action, status, detail):
    # NOTE: ``detail`` must never contain connection_json (it can hold session
    # tokens) or any credential.
    db.add(AvatarAuditLog(action=action, status=status, source="avatar-sessions",
                          detail=detail))
    db.commit()


def _serialize(s, include_connection=False):
    out = {
        "id": s.id,
        "avatar_profile_id": s.avatar_profile_id,
        "mode": s.mode,
        "status": s.status,
        "provider": s.provider,
        "disclosure_shown": bool(s.disclosure_shown),
        "consent_status": s.consent_status,
        "consent_detail": s.consent_detail,
        "result_detail": s.result_detail,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "ended_at": s.ended_at.isoformat() if s.ended_at else None,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }
    if include_connection and s.connection_json:
        try:
            out["connection"] = json.loads(s.connection_json)
        except (json.JSONDecodeError, ValueError):
            out["connection"] = None
    return out


def _load_profile(db, profile_id):
    p = db.query(AvatarProfile).filter(AvatarProfile.id == profile_id).first()
    if p is None:
        raise ValueError("Avatar profile %s not found." % profile_id)
    if not p.active:
        raise ValueError("Avatar profile '%s' is inactive." % p.name)
    return p


def _voice_ok():
    from src.avatar.voice import voice_configured
    return voice_configured()


def _avatar_ok(provider):
    from src.avatar.bridge import avatar_configured
    return avatar_configured(provider)


# A negotiated connection descriptor can carry provider ROOM SECRETS (a LiveKit
# ``access_token``, a WHEP bearer token, TURN credentials, an SDP offer). Those
# must NEVER be written to the database — the browser receives the FULL
# descriptor in the start/consent RESPONSE only (in-memory). Only this minimal,
# non-secret routing subset — what the SERVER itself needs later, e.g. the
# HeyGen ``session_id`` for ``streaming.start`` — is persisted.
_PERSISTABLE_CONNECTION_KEYS = ("session_id", "provider", "transport")


def _persistable_connection(conn):
    """Strip every token-bearing field from a connection descriptor before it is
    stored, keeping only non-secret routing the server reuses later (pillar #3)."""
    if not isinstance(conn, dict):
        return {}
    return {k: conn[k] for k in _PERSISTABLE_CONNECTION_KEYS if k in conn}


def _activate(db, session, profile):
    """Negotiate the bridge (if an avatar backend is configured) or degrade to
    voice-only. Consent is already committed before this runs.

    Returns the FULL connection descriptor (incl. any room token) for the active
    avatar_video response, or ``None`` for voice-only. The returned descriptor is
    for the immediate HTTP response ONLY; it is never persisted or logged."""
    # Defense-in-depth: re-check the STORED session against the profile's policy.
    try:
        ensure_call_compliance(profile, session)
    except (DisclosureRequired, ConsentRequired) as e:
        session.status = "blocked"
        session.result_detail = str(e)
        db.commit()
        _audit(db, "session_blocked", "blocked",
               "Session #%d blocked: %s" % (session.id, e))
        raise

    voice_ok = _voice_ok()
    avatar_provider = profile.avatar_provider

    # Try the avatar backend first when the profile selects one and it's configured.
    if avatar_provider and _avatar_ok(avatar_provider):
        from src.avatar.bridge import create_session as bridge_create
        avatar_config = None
        if profile.avatar_config_json:
            try:
                avatar_config = json.loads(profile.avatar_config_json)
            except (json.JSONDecodeError, ValueError):
                avatar_config = None
        try:
            conn = bridge_create(avatar_provider, persona=profile.persona,
                                 avatar_config=avatar_config, voice_id=profile.voice_id)
        except AvatarNotConfigured:
            # Race: lost its config between the check and the call — fall through.
            pass
        except (AvatarAuthError, AvatarProviderError) as e:
            session.status = "needs_provider"
            session.result_detail = "Avatar bridge unavailable: %s" % e
            db.commit()
            _audit(db, "session_needs_provider", "blocked",
                   "Session #%d avatar bridge failed; fix the backend and start a "
                   "new session." % session.id)
            raise
        else:
            full_conn = conn.get("connection") or {}
            session.status = "active"
            session.mode = "avatar_video"
            session.provider = conn.get("provider")
            # Persist ONLY non-secret routing — the room token never touches the DB.
            session.connection_json = json.dumps(_persistable_connection(full_conn))
            session.result_detail = conn.get("detail")
            session.started_at = datetime.utcnow()
            db.commit()
            db.refresh(session)
            # connection info deliberately excluded from the audit detail.
            _audit(db, "session_active", "ok",
                   "Session #%d active (avatar_video via %s)."
                   % (session.id, session.provider))
            # Full descriptor (incl. token) returned for THIS response only.
            return full_conn

    # No avatar backend (configured or reachable) — degrade honestly to voice-only.
    if voice_ok:
        session.status = "active"
        session.mode = "voice_only"
        session.provider = "elevenlabs"
        session.connection_json = None
        session.started_at = datetime.utcnow()
        session.result_detail = (
            "Voice-only: no avatar backend configured. Realistic video runs on the "
            "external Azure/GPU stack." if not avatar_provider else
            "Voice-only fallback: avatar provider '%s' is not configured."
            % avatar_provider)
        db.commit()
        db.refresh(session)
        _audit(db, "session_voice_only", "ok",
               "Session #%d active (voice_only)." % session.id)
        return None

    # Nothing usable — fail loud.
    session.status = "needs_provider"
    session.result_detail = "No voice or avatar provider configured."
    db.commit()
    _audit(db, "session_needs_provider", "blocked",
           "Session #%d has no usable provider." % session.id)
    raise AvatarNotConfigured(
        "Neither a voice nor an avatar provider is configured for this profile.")


def start_session(db, profile_id):
    """Create a session. Shows disclosure; gates on consent before going active.

    If consent is required, the session is returned in ``pending_consent`` and the
    caller must record consent before it activates. If consent is NOT required it is
    auto-granted and the session activates immediately (still failing loud if no
    provider is configured at all)."""
    profile = _load_profile(db, profile_id)

    # Fail loud up front if there's nothing to run on either layer.
    if not _voice_ok() and not _avatar_ok(profile.avatar_provider):
        _audit(db, "session_not_configured", "blocked",
               "Profile #%d has no voice or avatar provider." % profile.id)
        raise AvatarNotConfigured(
            "This profile has no voice or avatar provider configured. Add an "
            "ElevenLabs key (voice) and/or an avatar backend, then try again.")

    consent_needed = bool(profile.consent_required)
    session = AvatarSession(
        avatar_profile_id=profile.id,
        status="pending_consent" if consent_needed else "active",
        # Disclosure is presented to the operator/callee now.
        disclosure_shown=True,
        consent_status="pending" if consent_needed else "granted",
        mode=None,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    _audit(db, "session_requested",
           "pending_consent" if consent_needed else "ok",
           "Session #%d started for profile '%s'%s."
           % (session.id, profile.name,
              " (awaiting consent)" if consent_needed else ""))

    result = {
        "disclosure": disclosure_text() if profile.disclosure_required else None,
        "consent_prompt": consent_prompt() if consent_needed else None,
    }
    live_conn = None
    if not consent_needed:
        live_conn = _activate(db, session, profile)
    result["session"] = _serialize(session)
    # Full descriptor (incl. token) goes ONLY to this response, never the DB.
    if live_conn:
        result["session"]["connection"] = live_conn
    return result


def record_consent(db, session_id, granted, detail=None):
    """Record recording consent for a pending session, then activate (if granted).

    Re-validates the STORED session and locks it (FOR UPDATE) so concurrent calls
    can't double-activate. Consent is committed BEFORE bridge negotiation."""
    session = (db.query(AvatarSession)
               .filter(AvatarSession.id == session_id)
               .with_for_update()
               .first())
    if session is None:
        raise ValueError("Session %s not found." % session_id)
    if session.status != "pending_consent":
        _audit(db, "session_consent_blocked", "blocked",
               "Refused consent on session #%d in status '%s'."
               % (session.id, session.status))
        raise PermissionError(
            "Session #%d is '%s', not awaiting consent." % (session.id, session.status))

    if not granted:
        session.consent_status = "denied"
        session.status = "denied"
        session.consent_detail = detail or "Consent denied."
        session.ended_at = datetime.utcnow()
        db.commit()
        db.refresh(session)
        _audit(db, "session_consent_denied", "ok",
               "Session #%d consent denied." % session.id)
        return _serialize(session)

    # Persist consent BEFORE any best-effort bridge negotiation (two-phase lesson).
    session.consent_status = "granted"
    session.consent_detail = detail or "Consent granted."
    db.commit()
    _audit(db, "session_consent_granted", "ok",
           "Session #%d consent granted." % session.id)

    profile = _load_profile(db, session.avatar_profile_id)
    live_conn = _activate(db, session, profile)
    out = _serialize(session)
    # Full descriptor (incl. token) goes ONLY to this response, never the DB.
    if live_conn:
        out["connection"] = live_conn
    return out


def retry_session(db, session_id):
    """Re-attempt activation for a session stuck in ``needs_provider``.

    When the avatar backend errors during negotiation the session is left in
    ``needs_provider`` with consent already ``granted`` (consent is committed
    BEFORE negotiation). Once the operator fixes the backend, this re-runs
    activation IN PLACE — re-validating the STORED session under a row lock
    (``FOR UPDATE``) — so the callee is never re-prompted for consent they
    already gave. Refuses any session that is not ``needs_provider`` or whose
    consent is not ``granted``, and audits the transition.

    Returns the FULL connection descriptor (incl. any room token) for THIS
    response ONLY; it is never persisted or logged (pillar #3)."""
    session = (db.query(AvatarSession)
               .filter(AvatarSession.id == session_id)
               .with_for_update()
               .first())
    if session is None:
        raise ValueError("Session %s not found." % session_id)
    if session.status != "needs_provider":
        _audit(db, "session_retry_blocked", "blocked",
               "Refused retry on session #%d in status '%s'."
               % (session.id, session.status))
        raise PermissionError(
            "Session #%d is '%s', not awaiting a provider retry."
            % (session.id, session.status))
    if session.consent_status != "granted":
        _audit(db, "session_retry_blocked", "blocked",
               "Refused retry on session #%d without granted consent (consent '%s')."
               % (session.id, session.consent_status))
        raise PermissionError(
            "Session #%d cannot be retried without granted consent." % session.id)

    profile = _load_profile(db, session.avatar_profile_id)
    # Record the retry intent WITHOUT committing: committing here would end the
    # transaction and release the FOR UPDATE lock BEFORE activation, letting two
    # concurrent retries both pass validation and double-negotiate the same
    # session. Adding the row (no commit) keeps the lock held; _activate commits
    # it together with the resulting state transition, on every path.
    db.add(AvatarAuditLog(
        action="session_retry", status="ok", source="avatar-sessions",
        detail="Session #%d retrying provider activation (consent already granted)."
               % session.id))
    # _activate re-validates compliance, re-negotiates the bridge (or degrades to
    # voice-only) under the still-held lock, and audits the resulting transition.
    # Consent is untouched.
    live_conn = _activate(db, session, profile)
    out = _serialize(session)
    # Full descriptor (incl. token) goes ONLY to this response, never the DB.
    if live_conn:
        out["connection"] = live_conn
    return out


def end_session(db, session_id):
    """End a session and clear its (ephemeral) connection info."""
    session = (db.query(AvatarSession)
               .filter(AvatarSession.id == session_id)
               .with_for_update()
               .first())
    if session is None:
        raise ValueError("Session %s not found." % session_id)
    if session.status in ("ended", "denied"):
        _audit(db, "session_end_blocked", "blocked",
               "Refused to end session #%d in status '%s'."
               % (session.id, session.status))
        raise PermissionError(
            "Session #%d is already '%s'." % (session.id, session.status))
    session.status = "ended"
    session.connection_json = None  # drop ephemeral provider session tokens
    session.ended_at = datetime.utcnow()
    db.commit()
    db.refresh(session)
    _audit(db, "session_ended", "ok", "Session #%d ended." % session.id)
    return _serialize(session)


def start_stream(db, session_id):
    """Begin provider media publishing for an already-active avatar_video session.

    The browser connects to the negotiated room/peer first, THEN calls this so the
    provider actually publishes the avatar's tracks (e.g. HeyGen ``streaming.start``,
    which needs the server-side key). Returns a SANITIZED status dict — the stored
    connection descriptor can hold session tokens, so it is never returned, logged,
    or written to the audit trail.
    """
    session = (db.query(AvatarSession)
               .filter(AvatarSession.id == session_id)
               .first())
    if session is None:
        raise ValueError("Session %s not found." % session_id)
    if session.status != "active" or session.mode != "avatar_video":
        _audit(db, "session_stream_blocked", "blocked",
               "Refused stream-start on session #%d (status '%s', mode %s)."
               % (session.id, session.status, session.mode))
        raise PermissionError(
            "Session #%d is '%s'/%s, not an active avatar_video call."
            % (session.id, session.status, session.mode))
    connection = None
    if session.connection_json:
        try:
            connection = json.loads(session.connection_json)
        except (json.JSONDecodeError, ValueError):
            connection = None
    from src.avatar.bridge import start_stream as bridge_start_stream
    bridge_start_stream(session.provider, connection or {})
    # connection (tokens) deliberately excluded from the audit detail.
    _audit(db, "session_stream_started", "ok",
           "Session #%d media stream started (%s)." % (session.id, session.provider))
    return {
        "id": session.id,
        "status": session.status,
        "mode": session.mode,
        "provider": session.provider,
    }


def get_session(db, session_id, include_connection=False):
    session = (db.query(AvatarSession)
               .filter(AvatarSession.id == session_id).first())
    if session is None:
        raise ValueError("Session %s not found." % session_id)
    return _serialize(session, include_connection=include_connection)


def list_sessions(db, limit=50, status=None):
    q = db.query(AvatarSession)
    if status:
        q = q.filter(AvatarSession.status == status)
    rows = q.order_by(AvatarSession.id.desc()).limit(int(limit)).all()
    return [_serialize(r) for r in rows]
