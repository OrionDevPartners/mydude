"""Attribute transactions/vendors to projects (LLCs).

Deterministic, governance-first: a transaction is only attributed when there is a
concrete signal — an explicit vendor->project rule, a vendor's default project, or
a project code appearing in the memo/name. Optional email receipts can corroborate
(boost confidence). When nothing matches we leave it ``unattributed`` — we never
guess.

When a vendor->project relation is established we write a RELATION-LEVEL claim to
the memory substrate (never the raw transaction memo), because the memory cloud
adapter may egress content externally. Postgres remains the system of record.
"""
import logging
import re

from src.models import (
    FinanceProject, FinanceVendor, VendorProjectRule, FinanceTransaction,
    FinanceAuditLog,
)

logger = logging.getLogger(__name__)


def normalize(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", text.lower())).strip()


def _compact(text):
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _email_context():
    """Best-effort lowercase blob of recent receipt emails. None if unconfigured."""
    try:
        from src.bridge.email_imap import EmailReceiptReader
        reader = EmailReceiptReader()
        if not reader.available():
            return None
        receipts = reader.read_receipts(limit=50, lookback_days=365)
    except Exception as e:
        logger.debug("Email context unavailable: %s", e)
        return None
    parts = []
    for r in receipts or []:
        parts.append(normalize("%s %s %s" % (
            r.get("from", ""), r.get("subject", ""), r.get("body", ""))))
    return "\n".join(parts) if parts else None


def _attribute(txn, projects, rules, vendor):
    """Return (project_id, status, confidence, method) for a single transaction."""
    hay_norm = normalize("%s %s" % (txn.name or "", txn.memo or ""))
    hay_compact = _compact("%s %s" % (txn.name or "", txn.memo or ""))

    # 1. Explicit project code in the memo/name (strongest deterministic signal).
    for p in projects:
        code_compact = _compact(p.code)
        if code_compact and code_compact in hay_compact:
            return p.id, "attributed", 0.95, "code_match"

    # 2. Explicit vendor->project rule (normalized substring match).
    for rule in rules:
        m = normalize(rule.match_text)
        if m and m in hay_norm:
            return rule.project_id, "attributed", 0.85, "rule"

    # 3. Vendor default project.
    if vendor is not None and vendor.default_project_id:
        return vendor.default_project_id, "attributed", 0.8, "vendor_default"

    return None, "unattributed", 0.0, "none"


def run_attribution(db, txn_ids=None, write_memory=True):
    """Attribute pending/unattributed transactions. Returns count attributed.

    ``txn_ids`` limits the scope (e.g. only freshly-synced rows). Manually
    attributed rows are never overwritten.
    """
    projects = db.query(FinanceProject).filter(FinanceProject.active == True).all()  # noqa: E712
    rules = db.query(VendorProjectRule).all()
    vendors = {v.id: v for v in db.query(FinanceVendor).all()}
    projects_by_id = {p.id: p for p in projects}

    q = db.query(FinanceTransaction)
    if txn_ids:
        q = q.filter(FinanceTransaction.id.in_(list(txn_ids)))
    else:
        q = q.filter(FinanceTransaction.attribution_status == "unattributed")
    rows = q.all()

    email_blob = _email_context()
    attributed = 0
    edges_written = set()

    for txn in rows:
        if txn.attribution_method == "manual":
            continue
        vendor = vendors.get(txn.vendor_id) if txn.vendor_id else None
        project_id, status, confidence, method = _attribute(txn, projects, rules, vendor)

        # Optional email corroboration: boost confidence when a receipt mentions
        # both the vendor and the chosen project's code.
        if project_id and email_blob and vendor:
            proj = projects_by_id.get(project_id)
            vname = normalize(vendor.name)
            if proj and vname and vname in email_blob and _compact(proj.code) in _compact(email_blob):
                confidence = min(0.99, confidence + 0.05)
                method = method + "+email"

        txn.attribution_status = status
        txn.attribution_confidence = confidence
        txn.attribution_method = method
        txn.project_id = project_id

        if status == "attributed":
            attributed += 1
            if write_memory and vendor is not None:
                key = (vendor.id, project_id)
                if key not in edges_written:
                    edges_written.add(key)
                    _write_edge(vendor, projects_by_id.get(project_id), confidence, method)

    db.commit()
    db.add(FinanceAuditLog(
        action="attribution",
        status="ok",
        source="finance",
        detail="Attributed %d of %d transactions." % (attributed, len(rows)),
    ))
    db.commit()
    return attributed


def _write_edge(vendor, project, confidence, method):
    """Write a relation-level vendor->project claim to memory (never raw memos)."""
    if project is None:
        return
    try:
        from src.memory.substrate import get_substrate
        get_substrate().write_claim(
            content="Vendor '%s' is attributed to project %s (%s)."
                    % (vendor.name, project.code, project.name),
            category="finance",
            confidence=float(confidence),
            source="finance-sync",
            verified=False,
            metadata={
                "relation": "vendor_project",
                "vendor": vendor.name,
                "project_code": project.code,
                "method": method,
            },
        )
    except Exception as e:
        logger.debug("Memory edge write skipped: %s", e)
