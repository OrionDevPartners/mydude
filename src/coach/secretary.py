"""Two-phase, approval-gated secretary actions (draft email / text / booking).

Mirrors the finance write-back gate exactly: an action is created in
``pending_confirm`` and only ``confirm_action`` (after explicit operator approval)
dispatches it. Confirm re-validates the STORED request — it never trusts client
input at confirm — locks the row (``FOR UPDATE``) so two concurrent confirms can't
double-send, and audits every transition (including blocked/needs-provider/failed).

Dispatch goes through the provider-agnostic delivery layer. When the channel has
no provider configured the request is marked ``needs_provider`` (retryable after
the operator configures it) and we FAIL LOUD — never a faked send.
"""
import json
import logging
from datetime import datetime

from src.models import SecretaryRequest, CoachAuditLog

logger = logging.getLogger(__name__)

ALLOWED_KINDS = ("draft_email", "draft_text", "propose_booking")
_CHANNEL = {"draft_email": "email", "draft_text": "sms", "propose_booking": "calendar"}
# A request may be confirmed (or rejected) while pending OR while it is waiting on
# a provider the operator has since configured.
_CONFIRMABLE = ("pending_confirm", "needs_provider")


def _audit(db, action, status, detail):
    db.add(CoachAuditLog(action=action, status=status, source="coach-secretary",
                         detail=detail))
    db.commit()


def _serialize(req):
    payload = None
    if req.payload_json:
        try:
            payload = json.loads(req.payload_json)
        except (json.JSONDecodeError, ValueError):
            payload = None
    return {
        "id": req.id,
        "kind": req.kind,
        "channel": req.channel,
        "recipient": req.recipient,
        "subject": req.subject,
        "body": req.body,
        "payload": payload,
        "summary": req.summary,
        "status": req.status,
        "provider": req.provider,
        "result_detail": req.result_detail,
        "requested_at": req.requested_at.isoformat() if req.requested_at else None,
        "confirmed_at": req.confirmed_at.isoformat() if req.confirmed_at else None,
    }


def _validate(kind, recipient, body, payload):
    """Shared validation for request-time and confirm-time (stored) re-validation."""
    if kind not in ALLOWED_KINDS:
        raise ValueError("Unsupported action '%s'. Allowed: %s"
                         % (kind, ", ".join(ALLOWED_KINDS)))
    if kind in ("draft_email", "draft_text"):
        if not recipient:
            raise ValueError("A recipient is required.")
        if not body or not body.strip():
            raise ValueError("A message body is required.")
    else:  # propose_booking
        if (not isinstance(payload, dict) or not payload.get("summary")
                or not payload.get("start") or not payload.get("end")):
            raise ValueError(
                "A booking requires a payload with 'summary', 'start' and 'end'.")


def request_action(db, kind, recipient=None, subject=None, body=None,
                   payload=None, summary=None):
    """Create a pending action. Does NOT send anything."""
    _validate(kind, recipient, body, payload)
    req = SecretaryRequest(
        kind=kind,
        channel=_CHANNEL[kind],
        recipient=recipient or None,
        subject=subject or None,
        body=body or None,
        payload_json=json.dumps(payload) if payload else None,
        summary=summary or ("%s request" % kind),
        status="pending_confirm",
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    _audit(db, "action_requested", "pending_confirm",
           "%s #%d created and awaiting confirmation." % (kind, req.id))
    return _serialize(req)


def confirm_action(db, request_id):
    """Dispatch an action only if it is still awaiting confirmation.

    Re-validates the STORED request and dispatches via the provider-agnostic
    delivery layer. Refuses (and audits) anything not in a confirmable state.
    """
    req = (
        db.query(SecretaryRequest)
        .filter(SecretaryRequest.id == request_id)
        .with_for_update()
        .first()
    )
    if req is None:
        raise ValueError("Action %s not found." % request_id)

    if req.status not in _CONFIRMABLE:
        _audit(db, "action_confirm_blocked", "blocked",
               "Refused to execute action #%d in status '%s'." % (req.id, req.status))
        raise PermissionError(
            "Action #%d is '%s', not awaiting confirmation — refusing to execute."
            % (req.id, req.status))

    # Re-validate the STORED request; never trust client-provided data at confirm.
    payload = None
    if req.payload_json:
        try:
            payload = json.loads(req.payload_json)
        except json.JSONDecodeError:
            req.status = "failed"
            req.result_detail = "Stored payload is not valid JSON."
            db.commit()
            _audit(db, "action_failed", "error",
                   "Action #%d had a corrupt payload." % req.id)
            raise ValueError("Stored payload for action #%d is corrupt." % req.id)
    try:
        _validate(req.kind, req.recipient, req.body, payload)
    except ValueError:
        req.status = "failed"
        req.result_detail = "Stored request failed re-validation."
        db.commit()
        _audit(db, "action_failed", "error",
               "Action #%d failed re-validation." % req.id)
        raise

    from src.coach.delivery import (
        dispatch, channel_configured, DeliveryNotConfigured, DeliveryError,
    )

    channel = req.channel
    if not channel_configured(channel):
        req.status = "needs_provider"
        req.result_detail = "No %s provider configured." % channel
        db.commit()
        _audit(db, "action_needs_provider", "blocked",
               "Action #%d needs a %s provider." % (req.id, channel))
        raise DeliveryNotConfigured(
            "No %s provider configured. Configure it in the vault, then confirm "
            "this action again." % channel)

    try:
        result = dispatch(channel, recipient=req.recipient, subject=req.subject,
                          body=req.body, payload=payload)
    except DeliveryNotConfigured as e:
        req.status = "needs_provider"
        req.result_detail = str(e)
        db.commit()
        _audit(db, "action_needs_provider", "blocked",
               "Action #%d: %s" % (req.id, e))
        raise
    except DeliveryError as e:
        req.status = "failed"
        req.result_detail = str(e)
        db.commit()
        _audit(db, "action_failed", "error", "Action #%d failed: %s" % (req.id, e))
        raise

    req.status = "sent"
    req.provider = result.get("provider")
    req.result_detail = result.get("detail")
    req.confirmed_at = datetime.utcnow()
    db.commit()
    db.refresh(req)
    _audit(db, "action_sent", "ok",
           "Action #%d (%s) dispatched via %s." % (req.id, req.kind, req.provider))
    return _serialize(req)


def reject_action(db, request_id):
    """Reject a pending/needs-provider action (audited)."""
    req = db.query(SecretaryRequest).filter(SecretaryRequest.id == request_id).first()
    if req is None:
        raise ValueError("Action %s not found." % request_id)
    if req.status not in _CONFIRMABLE:
        _audit(db, "action_reject_blocked", "blocked",
               "Refused to reject action #%d in status '%s'." % (req.id, req.status))
        raise PermissionError("Action #%d is '%s', not pending." % (req.id, req.status))
    req.status = "rejected"
    req.result_detail = "Rejected by operator."
    db.commit()
    db.refresh(req)
    _audit(db, "action_rejected", "ok", "Action #%d rejected." % req.id)
    return _serialize(req)


def list_actions(db, limit=50, status=None):
    q = db.query(SecretaryRequest)
    if status:
        q = q.filter(SecretaryRequest.status == status)
    rows = q.order_by(SecretaryRequest.id.desc()).limit(int(limit)).all()
    return [_serialize(r) for r in rows]
