"""Two-phase, approval-gated write-back to QuickBooks.

Mirrors the subscriptions cancel gate: a write is created in ``pending_confirm``
and only ``confirm_write`` (after explicit operator approval) executes it. Confirm
re-validates the STORED payload — it never trusts client input — and every step,
including blocked/rejected attempts, is written to ``FinanceAuditLog``.
"""
import json
import logging

from src.models import FinanceWriteRequest, FinanceAuditLog

logger = logging.getLogger(__name__)

ALLOWED_KINDS = ("categorize", "create_bill", "create_invoice")


def _audit(db, action, status, detail):
    db.add(FinanceAuditLog(action=action, status=status, source="finance-writeback",
                           detail=detail))
    db.commit()


def _serialize(req):
    return {
        "id": req.id,
        "kind": req.kind,
        "target_external_id": req.target_external_id,
        "summary": req.summary,
        "status": req.status,
        "result_detail": req.result_detail,
        "requested_at": req.requested_at.isoformat() if req.requested_at else None,
        "confirmed_at": req.confirmed_at.isoformat() if req.confirmed_at else None,
    }


def request_write(db, kind, target_external_id, payload, summary=None):
    """Create a pending write request. Does NOT touch QuickBooks."""
    if kind not in ALLOWED_KINDS:
        raise ValueError("Unsupported write kind '%s'. Allowed: %s"
                         % (kind, ", ".join(ALLOWED_KINDS)))
    if not isinstance(payload, dict) or not payload:
        raise ValueError("A non-empty JSON payload is required.")

    req = FinanceWriteRequest(
        kind=kind,
        target_external_id=target_external_id or None,
        payload_json=json.dumps(payload),
        summary=summary or "%s request" % kind,
        status="pending_confirm",
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    _audit(db, "write_requested", "pending_confirm",
           "Write #%d (%s) created and awaiting confirmation." % (req.id, kind))
    return _serialize(req)


def confirm_write(db, request_id):
    """Execute a write only if it is still ``pending_confirm``.

    Re-validates the stored payload and executes against QuickBooks. Refuses (and
    audits) any request not in ``pending_confirm``.

    Locks the request row (``FOR UPDATE``) so two concurrent confirms cannot both
    pass the status check and double-execute the outbound write.
    """
    req = (
        db.query(FinanceWriteRequest)
        .filter(FinanceWriteRequest.id == request_id)
        .with_for_update()
        .first()
    )
    if req is None:
        raise ValueError("Write request %s not found." % request_id)

    if req.status != "pending_confirm":
        _audit(db, "write_confirm_blocked", "blocked",
               "Refused to execute write #%d in status '%s'." % (req.id, req.status))
        raise PermissionError(
            "Write #%d is '%s', not awaiting confirmation — refusing to execute."
            % (req.id, req.status)
        )

    # Re-validate the STORED payload; never trust client-provided data at confirm.
    try:
        payload = json.loads(req.payload_json or "{}")
    except json.JSONDecodeError:
        req.status = "failed"
        req.result_detail = "Stored payload is not valid JSON."
        db.commit()
        _audit(db, "write_failed", "error", "Write #%d had a corrupt payload." % req.id)
        raise ValueError("Stored payload for write #%d is corrupt." % req.id)

    if req.kind not in ALLOWED_KINDS or not isinstance(payload, dict) or not payload:
        req.status = "failed"
        req.result_detail = "Stored request failed re-validation."
        db.commit()
        _audit(db, "write_failed", "error", "Write #%d failed re-validation." % req.id)
        raise ValueError("Write #%d failed re-validation." % req.id)

    from datetime import datetime
    from src.finance.client_quickbooks import QuickBooksClient
    from src.finance.providers import FinanceAuthError, FinanceProviderError, FinanceNotConfigured

    try:
        client = QuickBooksClient()
        if req.kind == "categorize":
            result = client.update_purchase(payload)
        elif req.kind == "create_bill":
            result = client.create_bill(payload)
        else:  # create_invoice
            result = client.create_invoice(payload)
    except (FinanceAuthError, FinanceProviderError, FinanceNotConfigured) as e:
        req.status = "failed"
        req.result_detail = str(e)
        db.commit()
        _audit(db, "write_failed", "error", "Write #%d failed: %s" % (req.id, e))
        raise

    req.status = "executed"
    req.confirmed_at = datetime.utcnow()
    req.result_detail = "Executed against QuickBooks."
    db.commit()
    _audit(db, "write_executed", "ok", "Write #%d (%s) executed." % (req.id, req.kind))
    db.refresh(req)
    return _serialize(req)


def reject_write(db, request_id):
    """Reject a pending write (audited). Refuses anything not pending."""
    req = db.query(FinanceWriteRequest).filter(FinanceWriteRequest.id == request_id).first()
    if req is None:
        raise ValueError("Write request %s not found." % request_id)
    if req.status != "pending_confirm":
        _audit(db, "write_reject_blocked", "blocked",
               "Refused to reject write #%d in status '%s'." % (req.id, req.status))
        raise PermissionError("Write #%d is '%s', not pending." % (req.id, req.status))
    req.status = "rejected"
    req.result_detail = "Rejected by operator."
    db.commit()
    _audit(db, "write_rejected", "ok", "Write #%d rejected." % req.id)
    db.refresh(req)
    return _serialize(req)


def list_writes(db, limit=50):
    rows = db.query(FinanceWriteRequest).order_by(
        FinanceWriteRequest.id.desc()).limit(limit).all()
    return [_serialize(r) for r in rows]
