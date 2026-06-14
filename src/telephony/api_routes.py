"""Public telephony webhooks (Task #66).

These endpoints are called by the telephony PROVIDER (e.g. Twilio), not by the
authenticated operator, so they deliberately do NOT use the session-auth
dependency. They are instead gated by provider webhook-signature verification
(``X-Twilio-Signature``): an unconfigured deployment cannot construct the client
to verify a signature and therefore rejects every webhook (fail closed). The
audio route is token-gated (the provider fetches ``<Play>`` media over an
unsigned GET) and serves ``no-store``.

Mounted under the main API router so the full paths are::

    POST /api/telephony/voice           — call answered (inbound or outbound)
    POST /api/telephony/gather          — a speech turn (caller said something)
    POST /api/telephony/status          — provider status callback
    GET  /api/telephony/audio/{token}   — synthesized TTS playback

Every spoken turn runs through the capability broker (telephony_turn /
telephony_receive_call) → contract → policy → audit + DecisionTrace, so no
ungoverned model output is ever spoken (governance pillars #1 + #4).
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse

from src.telephony.facade import (
    public_base_url,
    validate_webhook,
    twiml_gather,
    twiml_play_hangup,
    twiml_say,
    TelephonyNotConfigured,
)
from src.telephony.audio_store import get_audio

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telephony")

_XML = "application/xml"
# A short, honest line spoken when we cannot proceed (no fabricated content).
_UNAVAILABLE = ("Sorry, this assistant is unavailable right now. "
                "Please try again later. Goodbye.")
_NOT_IN_SERVICE = "Sorry, this number is not in service. Goodbye."

# Terminal provider call states (underscored to match the CallSession model).
_TERMINAL = {"completed", "failed", "busy", "no_answer", "canceled"}


def _broker():
    from src.swarm.broker import CapabilityBroker
    from src.swarm.integrations import Integrations
    from src.swarm.policy import PolicyEngine
    return CapabilityBroker(PolicyEngine(), Integrations())


def _signed_url(request: Request) -> str:
    """Reconstruct the absolute URL the provider signed.

    Behind the Replit proxy ``request.url`` carries the internal scheme/host, so
    we rebuild the externally-visible URL from the public base + path + query —
    exactly the URL we handed the provider as the webhook target.
    """
    base = public_base_url()  # raises TelephonyNotConfigured if unknown
    url = base + request.url.path
    if request.url.query:
        url += "?" + request.url.query
    return url


async def _verify(request: Request, form) -> bool:
    """Verify the provider webhook signature. Fail closed on any error."""
    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature:
        return False
    try:
        url = _signed_url(request)
    except TelephonyNotConfigured as e:
        logger.warning("[TELEPHONY] cannot verify webhook (no public base URL): %s", e)
        return False
    params = {k: v for k, v in form.items() if isinstance(v, str)}
    try:
        return bool(validate_webhook(url, params, signature))
    except TelephonyNotConfigured as e:
        # Provider not configured -> no way to verify -> reject every webhook.
        logger.warning("[TELEPHONY] rejecting webhook, provider not configured: %s", e)
        return False
    except Exception as e:  # noqa: BLE001 — any verify error rejects (fail closed)
        logger.warning("[TELEPHONY] webhook signature verification error: %s", e)
        return False


def _audio_url(token):
    """Absolute URL for a stored audio token, or None when no base URL exists."""
    if not token:
        return None
    try:
        return public_base_url() + "/api/telephony/audio/" + token
    except TelephonyNotConfigured:
        return None


def _gather_action(call_session_id: int) -> str:
    """The next-turn ``<Gather action>`` URL (absolute when a base URL exists).

    A relative URL is a valid fallback — the provider resolves it against the
    request URL — but absolute is preferred so the next webhook's signature
    reconstruction matches exactly.
    """
    path = "/api/telephony/gather?cs=%d" % call_session_id
    try:
        return public_base_url() + path
    except TelephonyNotConfigured:
        return path


async def _call_broker(capability: str, params: dict):
    """Invoke a governed capability. Returns (allowed, reason, data_dict)."""
    res = await _broker().request(capability, params)
    data = {}
    if res.output:
        try:
            data = json.loads(res.output)
        except (ValueError, TypeError):
            data = {}
    return res.decision.allowed, res.decision.reason, data


def _turn_twiml(turn: dict, action_url: str) -> str:
    """Render one governed turn result as TwiML (gather another turn, or hang up).

    Prefers synthesized audio (``<Play>``); when synthesis was unavailable it
    falls back to a provider ``<Say>`` of the (still governed) reply text —
    honest degradation, never silence and never raw ungoverned text.
    """
    play_url = _audio_url(turn.get("audio_token"))
    say_text = None if play_url else turn.get("reply_text")
    if turn.get("end_call"):
        return twiml_play_hangup(play_url=play_url, say_text=say_text)
    return twiml_gather(action_url, play_url=play_url, say_text=say_text)


@router.post("/voice")
async def telephony_voice(request: Request):
    """Provider answer webhook for an inbound or outbound call.

    Outbound calls carry our ``cs`` (call session id) query param. Inbound calls
    are routed to the owning bot by the dialed number via the governed
    telephony_receive_call capability. Either way the opener turn is governed and
    the response is a speech ``<Gather>``.
    """
    form = await request.form()
    if not await _verify(request, form):
        return Response(twiml_say(_UNAVAILABLE), media_type=_XML, status_code=403)

    cs_raw = request.query_params.get("cs")
    call_session_id = None
    if cs_raw:
        try:
            call_session_id = int(cs_raw)
        except (TypeError, ValueError):
            call_session_id = None

    if call_session_id is None:
        # Inbound: route the dialed number to its bot and open a session.
        allowed, reason, data = await _call_broker("telephony_receive_call", {
            "to_number": form.get("To"),
            "from_number": form.get("From"),
            "call_sid": form.get("CallSid"),
            "source": "telephony-webhook",
        })
        if not allowed:
            return Response(twiml_say(_UNAVAILABLE), media_type=_XML)
        if not data.get("ok"):
            return Response(twiml_say(_NOT_IN_SERVICE), media_type=_XML)
        call_session_id = data.get("call_session_id")

    allowed, reason, turn = await _call_broker("telephony_turn", {
        "call_session_id": call_session_id,
        "caller_text": None,
        "source": "telephony-webhook",
    })
    if not allowed or not turn.get("ok"):
        return Response(twiml_say(_UNAVAILABLE), media_type=_XML)

    return Response(_turn_twiml(turn, _gather_action(call_session_id)), media_type=_XML)


@router.post("/gather")
async def telephony_gather(request: Request):
    """A speech turn: the caller said something; govern + speak the reply."""
    form = await request.form()
    if not await _verify(request, form):
        return Response(twiml_say(_UNAVAILABLE), media_type=_XML, status_code=403)

    cs_raw = request.query_params.get("cs")
    try:
        call_session_id = int(cs_raw)
    except (TypeError, ValueError):
        return Response(twiml_say(_UNAVAILABLE), media_type=_XML, status_code=400)

    caller_text = form.get("SpeechResult") or form.get("Digits") or ""
    allowed, reason, turn = await _call_broker("telephony_turn", {
        "call_session_id": call_session_id,
        "caller_text": caller_text,
        "source": "telephony-webhook",
    })
    if not allowed or not turn.get("ok"):
        return Response(twiml_say(_UNAVAILABLE), media_type=_XML)

    return Response(_turn_twiml(turn, _gather_action(call_session_id)), media_type=_XML)


@router.post("/status")
async def telephony_status_callback(request: Request):
    """Provider status callback — update the CallSession lifecycle state."""
    form = await request.form()
    if not await _verify(request, form):
        return PlainTextResponse("forbidden", status_code=403)

    cs_raw = request.query_params.get("cs")
    try:
        call_session_id = int(cs_raw)
    except (TypeError, ValueError):
        return PlainTextResponse("", status_code=204)

    raw_status = (form.get("CallStatus") or "").strip().replace("-", "_")
    sid = (form.get("CallSid") or "").strip() or None

    from datetime import datetime
    from src.database import SessionLocal
    from src.models import CallSession
    db = SessionLocal()
    try:
        row = db.query(CallSession).filter(CallSession.id == call_session_id).first()
        if row:
            if raw_status:
                row.status = raw_status
            if sid and not row.provider_call_sid:
                row.provider_call_sid = sid
            if raw_status in _TERMINAL and not row.ended_at:
                row.ended_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()
    return PlainTextResponse("", status_code=204)


@router.get("/audio/{token}")
async def telephony_audio(token: str):
    """Serve short-lived synthesized audio for provider ``<Play>`` (token-gated)."""
    found = get_audio(token)
    if not found:
        return PlainTextResponse("not found", status_code=404)
    audio, content_type = found
    return Response(content=audio, media_type=content_type,
                    headers={"Cache-Control": "no-store"})
