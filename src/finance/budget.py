"""Per-project budget vs actuals with variance flags.

Sign convention: Plaid reports positive ``amount`` as money leaving the account
(spend). Actuals are the sum of attributed transaction amounts; budgets are the
sum of that project's budget lines.
"""
from src.models import FinanceProject, FinanceBudget, FinanceTransaction

_NEAR_LIMIT_PCT = 90.0
_LARGE_TXN_FRACTION = 0.5


def _budget_total(db, project_id):
    total = 0.0
    for b in db.query(FinanceBudget).filter(FinanceBudget.project_id == project_id).all():
        total += float(b.amount or 0.0)
    return total


def _project_summary(db, project):
    budget_total = _budget_total(db, project.id)
    txns = db.query(FinanceTransaction).filter(
        FinanceTransaction.project_id == project.id,
        FinanceTransaction.attribution_status == "attributed",
    ).all()
    actual_total = sum(float(t.amount or 0.0) for t in txns)
    txn_count = len(txns)
    largest = max((float(t.amount or 0.0) for t in txns), default=0.0)

    variance = budget_total - actual_total
    pct_used = round((actual_total / budget_total) * 100, 1) if budget_total > 0 else None

    flags = []
    if budget_total > 0 and actual_total > budget_total:
        flags.append("over_budget")
    elif pct_used is not None and pct_used >= _NEAR_LIMIT_PCT:
        flags.append("near_limit")
    if budget_total == 0 and actual_total > 0:
        flags.append("no_budget")
    if budget_total > 0 and largest > budget_total * _LARGE_TXN_FRACTION:
        flags.append("large_txn")

    return {
        "project_id": project.id,
        "code": project.code,
        "name": project.name,
        "llc": project.llc,
        "budget_total": round(budget_total, 2),
        "actual_total": round(actual_total, 2),
        "variance": round(variance, 2),
        "pct_used": pct_used,
        "txn_count": txn_count,
        "largest_txn": round(largest, 2),
        "flags": flags,
    }


def budget_vs_actuals(db):
    """Return a per-project budget/actuals report plus an unattributed summary."""
    projects = db.query(FinanceProject).filter(FinanceProject.active == True).all()  # noqa: E712
    rows = [_project_summary(db, p) for p in projects]

    unattributed = db.query(FinanceTransaction).filter(
        FinanceTransaction.attribution_status == "unattributed",
    ).all()
    unattributed_summary = {
        "count": len(unattributed),
        "total": round(sum(float(t.amount or 0.0) for t in unattributed), 2),
    }
    return {"projects": rows, "unattributed": unattributed_summary}
