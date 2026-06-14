"""Ingest financial data (read-only) and run attribution.

Plaid uses cursor-based ``/transactions/sync`` (idempotent, incremental); the
cursor is persisted in the settings store. Transactions are upserted on
``(source, external_id)``. Pending -> posted is handled via Plaid's ``removed``
list and the ``pending_transaction_id`` link. QuickBooks contributes vendor and
account entities. Every run is recorded as a ``FinanceSyncRun``.
"""
import json
import logging
from datetime import datetime

from src.models import (
    FinanceTransaction, FinanceVendor, FinanceSyncRun, FinanceAuditLog,
)
from src.finance.attribution import run_attribution, normalize
from src.finance.providers import (
    FinanceNotConfigured, FinanceAuthError, FinanceProviderError,
)

logger = logging.getLogger(__name__)

_CURSOR_KEY = "PLAID_TXN_CURSOR"


def _new_run(db, source, trigger):
    run = FinanceSyncRun(source=source, trigger=trigger, status="running")
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _finish_run(db, run, status, **counts):
    run.status = status
    run.finished_at = datetime.utcnow()
    for k, v in counts.items():
        setattr(run, k, v)
    db.commit()


def _parse_date(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(value, fmt)
        except (ValueError, TypeError):
            continue
    return None


def _upsert_vendor(db, source, external_id, name):
    if not name:
        return None
    vendor = None
    if external_id:
        vendor = db.query(FinanceVendor).filter(
            FinanceVendor.source == source,
            FinanceVendor.external_id == external_id,
        ).first()
    if vendor is None:
        vendor = db.query(FinanceVendor).filter(
            FinanceVendor.source == source,
            FinanceVendor.normalized_name == normalize(name),
        ).first()
    if vendor is None:
        vendor = FinanceVendor(source=source, external_id=external_id, name=name,
                               normalized_name=normalize(name))
        db.add(vendor)
        db.flush()
    else:
        vendor.name = name
        vendor.normalized_name = normalize(name)
        if external_id and not vendor.external_id:
            vendor.external_id = external_id
    return vendor


# --------------------------------------------------------------------------- #
# Plaid
# --------------------------------------------------------------------------- #

def _ingest_plaid_deltas(db, added, modified, removed, new_ids):
    """Apply one Item's added/modified/removed deltas. Returns the removed count
    and appends newly-pending transaction ids to ``new_ids`` (mutated in place)."""
    removed_count = 0
    for r in removed:
        ext = r.get("transaction_id") if isinstance(r, dict) else r
        if not ext:
            continue
        n = db.query(FinanceTransaction).filter(
            FinanceTransaction.source == "plaid",
            FinanceTransaction.external_id == ext,
        ).delete()
        removed_count += n or 0

    for txn in (added + modified):
        ext = txn.get("transaction_id")
        if not ext:
            continue
        merchant = txn.get("merchant_name") or txn.get("name")
        vendor = _upsert_vendor(db, "plaid", txn.get("merchant_entity_id"), merchant)
        pfc = txn.get("personal_finance_category") or {}
        category_raw = pfc.get("primary") or ", ".join(txn.get("category") or [])

        row = db.query(FinanceTransaction).filter(
            FinanceTransaction.source == "plaid",
            FinanceTransaction.external_id == ext,
        ).first()
        if row is None:
            row = FinanceTransaction(source="plaid", external_id=ext)
            db.add(row)
            newly_pending = True
        else:
            newly_pending = (row.attribution_status == "unattributed")

        row.account = txn.get("account_id")
        row.txn_date = _parse_date(txn.get("date"))
        row.amount = float(txn.get("amount") or 0.0)
        row.currency = txn.get("iso_currency_code") or txn.get("unofficial_currency_code") or "USD"
        row.name = merchant
        row.memo = txn.get("original_description") or txn.get("name")
        row.category_raw = category_raw or None
        row.pending = bool(txn.get("pending"))
        row.pending_external_id = txn.get("pending_transaction_id")
        row.vendor_id = vendor.id if vendor else None
        db.flush()

        # A posted txn supersedes its earlier pending row (belt-and-suspenders
        # alongside Plaid's ``removed`` list).
        if row.pending_external_id:
            db.query(FinanceTransaction).filter(
                FinanceTransaction.source == "plaid",
                FinanceTransaction.external_id == row.pending_external_id,
            ).delete()

        if newly_pending:
            new_ids.append(row.id)

    return removed_count


def sync_plaid(db, trigger="manual"):
    """Read-only ingest across every linked Plaid Item.

    Iterates ``plaid_items`` (stored per-Item tokens + the legacy single token).
    Each Item syncs with its own cursor — stored Items keep their cursor on the
    ``plaid_items`` row, the legacy token uses the global ``PLAID_TXN_CURSOR``
    setting. A per-Item auth/provider failure marks that Item and continues; the
    run only fails when every Item failed. Fails loud (``skipped``) when Plaid
    app credentials are missing or no bank is linked — never fabricates data."""
    run = _new_run(db, "plaid", trigger)
    try:
        from src.finance.client_plaid import PlaidClient
        from src.finance.providers import plaid_app_credentials, plaid_items
        from src.web.settings_store import get_setting, set_setting
        from src.models import PlaidItem

        app = plaid_app_credentials()  # fail loud if client_id/secret missing
        items = plaid_items(db)
        if not items:
            raise FinanceNotConfigured(
                "Plaid app credentials are set but no bank is linked yet. "
                "Click “Connect bank” to link an account."
            )

        total_ingested = 0
        total_removed = 0
        new_ids = []
        item_errors = []
        synced_any = False

        for it in items:
            client = PlaidClient(access_token=it["access_token"], app_creds=app)
            cursor = get_setting(_CURSOR_KEY) if it["is_legacy"] else it["cursor"]
            try:
                added, modified, removed, next_cursor = client.transactions_sync(cursor)
            except (FinanceAuthError, FinanceProviderError) as e:
                item_errors.append(str(e))
                if not it["is_legacy"] and it["db_id"]:
                    row = db.query(PlaidItem).filter(PlaidItem.id == it["db_id"]).first()
                    if row is not None:
                        row.status = "error"
                        row.last_error = str(e)[:500]
                        db.commit()
                continue

            total_ingested += len(added) + len(modified)
            total_removed += _ingest_plaid_deltas(db, added, modified, removed, new_ids)
            db.commit()
            synced_any = True

            if it["is_legacy"]:
                if next_cursor:
                    set_setting(_CURSOR_KEY, next_cursor)
            elif it["db_id"]:
                row = db.query(PlaidItem).filter(PlaidItem.id == it["db_id"]).first()
                if row is not None:
                    row.cursor = next_cursor
                    row.status = "active"
                    row.last_error = None
                    row.last_synced_at = datetime.utcnow()
                    db.commit()

        attributed = run_attribution(db, txn_ids=new_ids) if new_ids else 0

        if not synced_any and item_errors:
            # Every linked Item failed — surface loud rather than reporting success.
            msg = "; ".join(item_errors)
            _finish_run(db, run, "error", error=msg)
            return {"ok": False, "source": "plaid", "error": msg}

        _finish_run(db, run, "ok",
                    transactions_ingested=total_ingested,
                    removed_count=total_removed, attributed_count=attributed)
        result = {"ok": True, "source": "plaid",
                  "ingested": total_ingested, "removed": total_removed,
                  "attributed": attributed, "items": len(items)}
        if item_errors:
            result["partial_errors"] = item_errors
        return result

    except FinanceNotConfigured as e:
        db.rollback()
        _finish_run(db, run, "skipped", error=str(e))
        return {"ok": False, "skipped": True, "source": "plaid", "error": str(e)}
    except (FinanceAuthError, FinanceProviderError) as e:
        db.rollback()
        _finish_run(db, run, "error", error=str(e))
        return {"ok": False, "source": "plaid", "error": str(e)}


# --------------------------------------------------------------------------- #
# QuickBooks
# --------------------------------------------------------------------------- #

def sync_quickbooks(db, trigger="manual"):
    run = _new_run(db, "quickbooks", trigger)
    try:
        from src.finance.client_quickbooks import QuickBooksClient

        client = QuickBooksClient()
        vendors = client.fetch_vendors()
        accounts = client.fetch_accounts()
        for v in vendors:
            _upsert_vendor(db, "quickbooks", str(v.get("Id")), v.get("DisplayName"))
        db.commit()
        _finish_run(db, run, "ok",
                    entities_ingested=len(vendors) + len(accounts))
        return {"ok": True, "source": "quickbooks",
                "vendors": len(vendors), "accounts": len(accounts)}

    except FinanceNotConfigured as e:
        db.rollback()
        _finish_run(db, run, "skipped", error=str(e))
        return {"ok": False, "skipped": True, "source": "quickbooks", "error": str(e)}
    except (FinanceAuthError, FinanceProviderError) as e:
        db.rollback()
        _finish_run(db, run, "error", error=str(e))
        return {"ok": False, "source": "quickbooks", "error": str(e)}


def sync_all(db, trigger="manual"):
    """Run both providers (read-only). Returns a combined report.

    ``ok`` is True if at least one provider ingested successfully. When both are
    unconfigured the report fails loud with the providers' actionable messages.
    """
    plaid = sync_plaid(db, trigger)
    quickbooks = sync_quickbooks(db, trigger)
    any_ok = bool(plaid.get("ok") or quickbooks.get("ok"))
    errors = [r["error"] for r in (plaid, quickbooks)
              if not r.get("ok") and r.get("error")]
    db.add(FinanceAuditLog(
        action="sync", status="ok" if any_ok else "error", source="finance-sync",
        detail="trigger=%s plaid_ok=%s qbo_ok=%s"
               % (trigger, plaid.get("ok"), quickbooks.get("ok")),
    ))
    db.commit()
    return {"ok": any_ok, "trigger": trigger,
            "plaid": plaid, "quickbooks": quickbooks,
            "error": None if any_ok else " ".join(errors) or "No finance provider is configured."}
