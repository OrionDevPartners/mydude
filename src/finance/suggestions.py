"""Auto-suggested, approval-gated finance write-backs.

Builds DRAFT QuickBooks write requests (``categorize`` / ``create_bill``) from
REAL data only — never fabricated. Every draft is created in ``pending_confirm``
via :func:`writeback.request_write`, so it sits behind the exact same CONFIRM
gate as a hand-built write; **nothing is posted to QuickBooks here**. When the
grounding data is insufficient to build an *executable* payload we SKIP (with an
audited, sanitised reason) rather than emit a guess.

Governance (HARD pillars honoured here):
  1. No placeholders / never guess. A suggestion whose payload would always fail
     at confirm is a placeholder, so we only emit payloads grounded in real QBO
     ``Id``/``SyncToken``/account references and real transaction matches.
  2/3. Provider-agnostic + secrets decoupled: all QuickBooks/IMAP access is
     injected via factories (default to the real clients) and credentials are
     sourced inside those clients via the providers layer.
  4. Governed + audited: generation start/finish and every skip *category*
     (counts only — never raw memos, receipt bodies, or amounts) are written to
     ``FinanceAuditLog``.

Two suggestion kinds:

* ``categorize`` — for a real QBO Purchase whose expense line has no account (or
  an "Uncategorized" account), suggest the account that *this vendor's own
  history* dominantly uses. Emitted as a sparse Purchase update.
* ``create_bill`` — when a receipt email corroborates exactly one QBO-linked
  vendor and matches exactly one non-pending bank transaction (amount within a
  cent + a date window + vendor text), draft a QBO Bill using that vendor and its
  dominant historical expense account.
"""
import hashlib
import logging
import re
from collections import Counter, defaultdict
from email.utils import parsedate_to_datetime

from src.models import (
    FinanceVendor,
    FinanceTransaction,
    FinanceWriteRequest,
    FinanceAuditLog,
    FinanceProject,
)
from src.finance import writeback
from src.finance.attribution import normalize, run_attribution

logger = logging.getLogger(__name__)

# An amount like "$1,234.56" or "1234.56" — always two decimal places so we do
# not match bare integers (order numbers, zip codes, etc.).
_AMOUNT_RE = re.compile(r"\$?\s?(\d{1,3}(?:,\d{3})+\.\d{2}|\d+\.\d{2})")
_UNCATEGORIZED = "uncategor"   # substring marker for an "Uncategorized …" account
_DATE_WINDOW_DAYS = 5
_AMOUNT_TOLERANCE = 0.01
_TARGET_MAX = 120              # FinanceWriteRequest.target_external_id is String(120)
_EXPENSE_DETAIL = "AccountBasedExpenseLineDetail"


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #

def _audit(db, status, detail):
    db.add(FinanceAuditLog(action="suggestions_generate", status=status,
                           source="finance-suggestions", detail=detail))
    db.commit()


def _money(value):
    return "${:,.2f}".format(value)


def _account_ref(line):
    """The expense AccountRef id of a line, or ``None`` if absent."""
    detail = line.get(_EXPENSE_DETAIL) or {}
    ref = detail.get("AccountRef") or {}
    value = ref.get("value")
    return str(value) if value is not None else None


def _is_uncategorized(account_id, accounts_by_id):
    if not account_id:
        return True
    name = (accounts_by_id.get(str(account_id)) or {}).get("Name") or ""
    return _UNCATEGORIZED in name.lower()


def _expense_lines(purchase):
    return [ln for ln in (purchase.get("Line") or [])
            if ln.get("DetailType") == _EXPENSE_DETAIL]


def _entity_id(purchase):
    ent = purchase.get("EntityRef") or {}
    value = ent.get("value")
    return str(value) if value is not None else None


def _parse_amounts(text):
    """Every two-decimal money amount found in ``text`` (deduped, as floats)."""
    out = set()
    for m in _AMOUNT_RE.finditer(text or ""):
        try:
            out.add(round(float(m.group(1).replace(",", "")), 2))
        except ValueError:
            continue
    return out


def _parse_date(value):
    """Parse an RFC-2822 email ``Date`` header to a naive datetime, or ``None``."""
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(tz=None).replace(tzinfo=None)
    return dt


def _token_in(needle, haystack):
    """True if normalized ``needle`` appears in ``haystack`` on token boundaries.

    Both inputs must already be :func:`normalize`-d (space-separated ``[a-z0-9 ]``
    tokens). Padding with spaces makes "am" match only a standalone "am" token,
    not the "am" inside "amazon" — substring matching here would let a short
    vendor name spuriously match an unrelated receipt/transaction, fabricating a
    bill (Pillar 1 violation).
    """
    if not needle or not haystack:
        return False
    return (" %s " % needle) in (" %s " % haystack)


def _already(db, kind, target):
    """True if a write of this kind/target already exists in ANY status.

    Dedupe is intentionally status-agnostic: a previously rejected/executed
    suggestion must not be regenerated on the next run.
    """
    return db.query(FinanceWriteRequest.id).filter(
        FinanceWriteRequest.kind == kind,
        FinanceWriteRequest.target_external_id == target,
    ).first() is not None


# --------------------------------------------------------------------------- #
# vendor history -> dominant expense account
# --------------------------------------------------------------------------- #

def _vendor_dominant_accounts(purchases, accounts_by_id):
    """Map QBO entity id -> the expense account that entity dominantly uses.

    A history account counts only when it is a real, NON-uncategorized expense
    account. An entity gets a dominant account when either it has a single
    observed account (no conflict) or one account holds >=2 observations AND
    >=70% share. Ambiguous histories yield no entry (so the caller skips).
    """
    counts = defaultdict(Counter)
    for p in purchases:
        vid = _entity_id(p)
        if not vid:
            continue
        for line in _expense_lines(p):
            acct = _account_ref(line)
            if not acct or _is_uncategorized(acct, accounts_by_id):
                continue
            counts[vid][acct] += 1

    dominant = {}
    for vid, counter in counts.items():
        total = sum(counter.values())
        acct, n = counter.most_common(1)[0]
        if total == 1 or (n >= 2 and (n / total) >= 0.7):
            dominant[vid] = acct
    return dominant


def _build_categorize_payload(purchase, account_id, accounts_by_id):
    """A sparse Purchase update setting every uncategorized expense line to
    ``account_id``. Returns ``(payload, changed_count)``.

    The full ``Line`` array is preserved (QBO replaces lines wholesale on a
    Purchase update) — only the uncategorized expense lines' ``AccountRef`` is
    rewritten.
    """
    lines = []
    changed = 0
    for line in purchase.get("Line") or []:
        new_line = dict(line)
        if line.get("DetailType") == _EXPENSE_DETAIL:
            acct = _account_ref(line)
            if not acct or _is_uncategorized(acct, accounts_by_id):
                detail = dict(line.get(_EXPENSE_DETAIL) or {})
                detail["AccountRef"] = {"value": str(account_id)}
                new_line[_EXPENSE_DETAIL] = detail
                changed += 1
        lines.append(new_line)
    payload = {
        "Id": str(purchase.get("Id")),
        "SyncToken": str(purchase.get("SyncToken")),
        "sparse": True,
        "Line": lines,
    }
    return payload, changed


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #

def generate_suggestions(db, qbo_client_factory=None, email_reader_factory=None,
                         limit=100):
    """Generate pending categorize/create_bill suggestions from live data.

    Returns ``{"ok", "configured", "counts", "created", "skipped", "message"}``.
    ``created`` is a list of serialized FinanceWriteRequest dicts (all
    ``pending_confirm``); ``skipped`` is a ``reason -> count`` map.
    """
    if qbo_client_factory is None:
        from src.finance.client_quickbooks import QuickBooksClient
        qbo_client_factory = QuickBooksClient
    if email_reader_factory is None:
        from src.bridge.email_imap import EmailReceiptReader
        email_reader_factory = EmailReceiptReader

    from src.finance.providers import (
        FinanceNotConfigured, FinanceAuthError, FinanceProviderError,
    )

    created = []
    skipped = defaultdict(int)

    def skip(reason):
        skipped[reason] += 1

    _audit(db, "running", "Generating finance suggestions (categorize + bills).")

    # Refresh attribution so vendor->project context in summaries is current.
    # Best-effort: a failure here must never block suggestion generation.
    try:
        run_attribution(db, write_memory=True)
    except Exception as e:  # noqa: BLE001
        logger.debug("attribution refresh skipped: %s", e)

    # QuickBooks grounding. Fail loud (actionable) if unconfigured.
    try:
        client = qbo_client_factory()
        accounts = client.fetch_accounts()
        purchases = client.fetch_purchases()
    except FinanceNotConfigured as e:
        _audit(db, "skipped", "QuickBooks not configured: %s" % e)
        return {"ok": False, "configured": False,
                "counts": {"categorize": 0, "create_bill": 0, "total": 0},
                "created": [], "skipped": dict(skipped), "message": str(e)}
    except (FinanceAuthError, FinanceProviderError) as e:
        _audit(db, "error", "QuickBooks read failed: %s" % e)
        raise

    accounts_by_id = {str(a.get("Id")): a for a in accounts}
    dominant = _vendor_dominant_accounts(purchases, accounts_by_id)

    qbo_vendors = {
        v.external_id: v
        for v in db.query(FinanceVendor)
        .filter(FinanceVendor.source == "quickbooks").all()
        if v.external_id
    }
    projects_by_id = {p.id: p for p in db.query(FinanceProject).all()}

    _suggest_categorize(db, purchases, accounts_by_id, dominant, qbo_vendors,
                        projects_by_id, created, skip, limit)

    if len(created) < limit:
        _suggest_bills(db, dominant, accounts_by_id, qbo_vendors,
                       email_reader_factory, created, skip, limit)

    counts = {
        "categorize": sum(1 for c in created if c["kind"] == "categorize"),
        "create_bill": sum(1 for c in created if c["kind"] == "create_bill"),
        "total": len(created),
    }
    skip_detail = ", ".join("%s=%d" % (k, v) for k, v in sorted(skipped.items()))
    _audit(db, "ok",
           "Created %d suggestion(s): %d categorize, %d bill. Skipped: %s."
           % (counts["total"], counts["categorize"], counts["create_bill"],
              skip_detail or "none"))
    return {"ok": True, "configured": True, "counts": counts,
            "created": created, "skipped": dict(skipped),
            "message": "Created %d suggestion(s)." % counts["total"]}


# --------------------------------------------------------------------------- #
# categorize
# --------------------------------------------------------------------------- #

def _suggest_categorize(db, purchases, accounts_by_id, dominant, qbo_vendors,
                        projects_by_id, created, skip, limit):
    for p in purchases:
        if len(created) >= limit:
            return
        exp_lines = _expense_lines(p)
        if not exp_lines:
            continue
        # Only purchases that actually need categorizing.
        if not any(_is_uncategorized(_account_ref(ln), accounts_by_id)
                   for ln in exp_lines):
            continue

        pid = str(p.get("Id") or "")
        if not pid:
            skip("missing_purchase_id")
            continue
        if not p.get("SyncToken"):
            skip("missing_sync_token")
            continue
        vid = _entity_id(p)
        if not vid:
            skip("no_qbo_vendor")
            continue
        account_id = dominant.get(vid)
        if not account_id:
            skip("no_account_history")
            continue

        target = "qbo_purchase:%s" % pid
        if _already(db, "categorize", target):
            skip("already_suggested")
            continue

        payload, changed = _build_categorize_payload(p, account_id, accounts_by_id)
        if not changed:
            skip("nothing_to_change")
            continue

        acct_name = (accounts_by_id.get(str(account_id)) or {}).get("Name") \
            or ("account %s" % account_id)
        ent = p.get("EntityRef") or {}
        fv = qbo_vendors.get(vid)
        vname = ent.get("name") or (fv.name if fv else "this vendor")
        summary = ("Auto-suggested: categorize %s purchase as '%s' "
                   "(from %s's expense history)." % (vname, acct_name, vname))
        if fv and fv.default_project_id and projects_by_id.get(fv.default_project_id):
            summary += " Vendor default project %s." \
                % projects_by_id[fv.default_project_id].code

        created.append(writeback.request_write(db, "categorize", target,
                                               payload, summary))


# --------------------------------------------------------------------------- #
# create_bill (receipt -> transaction)
# --------------------------------------------------------------------------- #

def _suggest_bills(db, dominant, accounts_by_id, qbo_vendors,
                   email_reader_factory, created, skip, limit):
    try:
        reader = email_reader_factory()
        if not reader.available():
            skip("email_not_configured")
            return
        receipts = reader.read_receipts(limit=50, lookback_days=365)
    except Exception as e:  # noqa: BLE001
        logger.debug("receipt read skipped: %s", e)
        skip("email_unavailable")
        return

    # Only vendors with BOTH a QBO link and a confident historical account can
    # ground a fully-valid Bill line.
    candidates = [
        (extid, v, normalize(v.name))
        for extid, v in qbo_vendors.items()
        if extid in dominant and normalize(v.name)
    ]
    if not candidates:
        if receipts:
            skip("no_groundable_vendor")
        return

    txns = (db.query(FinanceTransaction)
            .filter(FinanceTransaction.source == "plaid",
                    FinanceTransaction.pending.is_(False)).all())

    for r in receipts or []:
        if len(created) >= limit:
            return
        blob = normalize("%s %s %s" % (r.get("from", ""), r.get("subject", ""),
                                       r.get("body", "")))
        matched = [(extid, v) for (extid, v, nname) in candidates
                   if _token_in(nname, blob)]
        if not matched:
            skip("no_qbo_vendor")
            continue
        if len(matched) > 1:
            skip("ambiguous_vendor")
            continue
        extid, vendor = matched[0]

        amounts = _parse_amounts("%s %s" % (r.get("subject", ""), r.get("body", "")))
        if not amounts:
            skip("no_amount")
            continue
        rdate = _parse_date(r.get("date"))
        if rdate is None:
            skip("no_date")
            continue

        vname_norm = normalize(vendor.name)
        txn_matches = [
            t for t in txns
            if t.txn_date is not None
            and _amount_matches(t.amount, amounts)
            and abs((t.txn_date.date() - rdate.date()).days) <= _DATE_WINDOW_DAYS
            and _token_in(vname_norm, normalize("%s %s" % (t.name or "", t.memo or "")))
        ]
        if len(txn_matches) != 1:
            skip("ambiguous_receipt_match" if txn_matches else "no_txn_match")
            continue
        txn = txn_matches[0]

        digest = hashlib.sha256(
            ("%s|%s|%s" % (r.get("from", ""), r.get("subject", ""),
                           r.get("date", ""))).encode("utf-8")
        ).hexdigest()[:12]
        ext = (txn.external_id or "")[:80]
        target = ("suggestion_bill:%s:%s" % (ext, digest))[:_TARGET_MAX]
        if _already(db, "create_bill", target):
            skip("already_suggested")
            continue

        account_id = dominant[extid]
        amount = round(abs(txn.amount), 2)
        txn_date = txn.txn_date.date().isoformat() if txn.txn_date \
            else rdate.date().isoformat()
        payload = {
            "VendorRef": {"value": str(extid)},
            "TxnDate": txn_date,
            "Line": [{
                "Amount": amount,
                "DetailType": _EXPENSE_DETAIL,
                _EXPENSE_DETAIL: {"AccountRef": {"value": str(account_id)}},
            }],
        }
        acct_name = (accounts_by_id.get(str(account_id)) or {}).get("Name") \
            or ("account %s" % account_id)
        summary = ("Auto-suggested: bill for %s — %s to '%s' "
                   "(receipt matched bank txn %s)."
                   % (vendor.name, _money(amount), acct_name, txn.external_id))
        created.append(writeback.request_write(db, "create_bill", target,
                                               payload, summary))


def _amount_matches(txn_amount, amounts):
    a = round(abs(txn_amount), 2)
    return any(abs(a - x) <= _AMOUNT_TOLERANCE for x in amounts)
