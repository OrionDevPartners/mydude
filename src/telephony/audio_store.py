"""Short-lived, token-gated TTS audio store for telephony playback (Task #66).

Telephony providers fetch ``<Play>`` audio over plain HTTP GET (no signature),
so each synthesized MP3 is parked in ``CallAudio`` behind a high-entropy token
with a TTL and served ``no-store``. Rows are disposable, carry no secrets, and
are pruned after expiry; the token is unguessable so the URL is the capability.
"""
import logging
import secrets as _secrets
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_DEFAULT_TTL_SECONDS = 900  # 15 minutes — comfortably longer than any single call


def store_audio(audio_bytes, content_type="audio/mpeg",
                call_session_id=None, ttl_seconds=_DEFAULT_TTL_SECONDS):
    """Persist ``audio_bytes`` and return an unguessable token. Fail loud on empty."""
    if not audio_bytes:
        raise ValueError("Refusing to store empty audio.")
    from src.database import SessionLocal
    from src.models import CallAudio

    token = _secrets.token_urlsafe(32)[:64]
    db = SessionLocal()
    try:
        db.add(CallAudio(
            token=token,
            call_session_id=call_session_id,
            content_type=content_type or "audio/mpeg",
            audio_bytes=audio_bytes,
            expires_at=datetime.utcnow() + timedelta(seconds=int(ttl_seconds)),
        ))
        db.commit()
    finally:
        db.close()
    prune_expired()  # best-effort housekeeping
    return token


def get_audio(token):
    """Return ``(audio_bytes, content_type)`` for a live token, else ``None``.

    Expired or unknown tokens return ``None`` so the route answers 404 — never a
    placeholder or stale clip.
    """
    token = (token or "").strip()
    if not token:
        return None
    from src.database import SessionLocal
    from src.models import CallAudio

    db = SessionLocal()
    try:
        row = db.query(CallAudio).filter(CallAudio.token == token).first()
        if not row:
            return None
        if row.expires_at and row.expires_at < datetime.utcnow():
            return None
        return row.audio_bytes, (row.content_type or "audio/mpeg")
    finally:
        db.close()


def prune_expired():
    """Delete expired audio rows. Best-effort; never raises into the caller."""
    try:
        from src.database import SessionLocal
        from src.models import CallAudio
        db = SessionLocal()
        try:
            db.query(CallAudio).filter(
                CallAudio.expires_at.isnot(None),
                CallAudio.expires_at < datetime.utcnow(),
            ).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001 — housekeeping must never break a call
        logger.debug("CallAudio prune failed: %s", e)
