"""Unit tests for finance attribution (``src/finance/attribution.py``).

Attribution is the correctness core of the finance sub-stack: it decides which
project/LLC a transaction belongs to. The governing rule is "never guess — leave
it ``unattributed`` when no concrete signal matches." These tests pin that rule
and the precedence order between the three deterministic signals:

    project code in memo/name  >  explicit vendor->project rule  >  vendor default

They are fully hermetic: each test runs against a fresh in-memory SQLite database
(the models use only portable column types) and the optional email-corroboration
hook is stubbed out — so there is no network, no shared-DB interference, and no
live credentials.

Runnable two ways:
  * ``python tests/test_finance_attribution.py``  (standalone, non-zero on failure)
  * ``pytest tests/test_finance_attribution.py``   (test_* functions; no plugins)
"""
import os
import sys
import itertools
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database import Base
from src import models  # noqa: F401  (registers all tables on Base.metadata)
from src.models import (
    FinanceProject, FinanceVendor, VendorProjectRule, FinanceTransaction,
)
from src.finance import attribution


# -- helpers -----------------------------------------------------------------

@contextmanager
def _patch(obj, name, value):
    missing = object()
    orig = getattr(obj, name, missing)
    setattr(obj, name, value)
    try:
        yield
    finally:
        if orig is missing:
            delattr(obj, name)
        else:
            setattr(obj, name, orig)


def _session():
    """A fresh, isolated in-memory SQLite session with all tables created."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


_EXT = itertools.count(1)


def _project(db, code, name=None, active=True):
    p = FinanceProject(code=code, name=name or code, active=active)
    db.add(p)
    db.flush()
    return p


def _vendor(db, name, default_project_id=None):
    v = FinanceVendor(source="manual", name=name,
                      normalized_name=attribution.normalize(name),
                      default_project_id=default_project_id)
    db.add(v)
    db.flush()
    return v


def _rule(db, match_text, project_id):
    r = VendorProjectRule(match_text=match_text, project_id=project_id)
    db.add(r)
    db.flush()
    return r


def _txn(db, name=None, memo=None, vendor_id=None, status="unattributed",
         method=None, project_id=None):
    t = FinanceTransaction(
        source="plaid", external_id="ext-%d" % next(_EXT),
        name=name, memo=memo, vendor_id=vendor_id, amount=10.0,
        attribution_status=status, attribution_method=method, project_id=project_id)
    db.add(t)
    db.flush()
    return t


def _attribute(db, txn_ids=None):
    # Email corroboration is an optional, environment-dependent confidence
    # booster; stub it so the deterministic decision is what is under test.
    with _patch(attribution, "_email_context", lambda: None):
        return attribution.run_attribution(db, txn_ids=txn_ids, write_memory=False)


# -- precedence: code > rule > vendor_default --------------------------------

def test_code_match_beats_rule_and_vendor_default():
    db = _session()
    try:
        p_code = _project(db, "ZZQ-CODE-7")
        p_rule = _project(db, "RULE-ONLY-1")
        p_vendor = _project(db, "VEND-ONLY-2")
        v = _vendor(db, "Acme Tools", default_project_id=p_vendor.id)
        _rule(db, "acme tools", p_rule.id)
        # The memo carries the project code (compact "zzqcode7"); the name+vendor
        # also satisfy the rule and the vendor default. Code must still win.
        t = _txn(db, name="Acme Tools", memo="Paid invoice ZZQCODE7 retainer",
                 vendor_id=v.id)
        db.commit()

        n = _attribute(db, [t.id])
        db.refresh(t)
        assert n == 1, n
        assert t.project_id == p_code.id, (t.project_id, p_code.id)
        assert t.attribution_method == "code_match", t.attribution_method
        assert t.attribution_status == "attributed", t.attribution_status
        assert abs(t.attribution_confidence - 0.95) < 1e-9, t.attribution_confidence
    finally:
        db.close()


def test_rule_beats_vendor_default():
    db = _session()
    try:
        p_rule = _project(db, "RULE-1")
        p_vendor = _project(db, "VEND-2")
        v = _vendor(db, "Acme Tools", default_project_id=p_vendor.id)
        _rule(db, "acme", p_rule.id)
        # No project code in the text -> the explicit rule outranks the default.
        t = _txn(db, name="Acme Tools", memo="monthly retainer", vendor_id=v.id)
        db.commit()

        n = _attribute(db, [t.id])
        db.refresh(t)
        assert n == 1, n
        assert t.project_id == p_rule.id, (t.project_id, p_rule.id)
        assert t.attribution_method == "rule", t.attribution_method
        assert abs(t.attribution_confidence - 0.85) < 1e-9, t.attribution_confidence
    finally:
        db.close()


def test_vendor_default_used_when_no_code_or_rule():
    db = _session()
    try:
        p_vendor = _project(db, "VEND-3")
        p_other = _project(db, "OTHER-9")
        v = _vendor(db, "Northwind Supplies", default_project_id=p_vendor.id)
        # A rule exists but does NOT match this transaction.
        _rule(db, "totally different vendor", p_other.id)
        t = _txn(db, name="Northwind Supplies", memo="invoice 8842", vendor_id=v.id)
        db.commit()

        n = _attribute(db, [t.id])
        db.refresh(t)
        assert n == 1, n
        assert t.project_id == p_vendor.id, (t.project_id, p_vendor.id)
        assert t.attribution_method == "vendor_default", t.attribution_method
        assert abs(t.attribution_confidence - 0.8) < 1e-9, t.attribution_confidence
    finally:
        db.close()


# -- the "never guess" guarantee ---------------------------------------------

def test_unattributed_when_no_signal_matches():
    db = _session()
    try:
        # Projects and a rule exist, but nothing matches this transaction and the
        # vendor has no default project. The result must be left unattributed.
        _project(db, "NSB-1194")
        p = _project(db, "XYZ-5")
        _rule(db, "stripe", p.id)
        v = _vendor(db, "Mystery Merchant LLC", default_project_id=None)
        t = _txn(db, name="Mystery Merchant LLC", memo="no codes here",
                 vendor_id=v.id)
        db.commit()

        n = _attribute(db, [t.id])
        db.refresh(t)
        assert n == 0, n
        assert t.project_id is None, t.project_id
        assert t.attribution_status == "unattributed", t.attribution_status
        assert t.attribution_method == "none", t.attribution_method
        assert t.attribution_confidence == 0.0, t.attribution_confidence
    finally:
        db.close()


def test_competing_same_tier_codes_attribute_to_a_candidate():
    db = _session()
    try:
        # Two different project codes BOTH appear in the memo. The implemented
        # rule is "attribute on a concrete signal" (here, the code tier) — NOT
        # "abstain on competing same-tier matches". So the txn must end up
        # attributed to one of the two matching projects: never silently
        # unattributed, and never a third (non-matching) project. This pins the
        # real behavior so a future change to the precedence engine is caught.
        p1 = _project(db, "ALPHA-1")
        p2 = _project(db, "BETA-2")
        _project(db, "GAMMA-3")  # non-matching decoy
        t = _txn(db, name="Vendor", memo="charges for ALPHA1 and BETA2 work")
        db.commit()

        n = _attribute(db, [t.id])
        db.refresh(t)
        assert n == 1, n
        assert t.attribution_status == "attributed", t.attribution_status
        assert t.attribution_method == "code_match", t.attribution_method
        assert t.project_id in (p1.id, p2.id), (t.project_id, p1.id, p2.id)
    finally:
        db.close()


def test_ambiguous_vendorless_txn_stays_unattributed():
    db = _session()
    try:
        # No vendor at all and no code/rule signal -> never guessed.
        _project(db, "AAA-1")
        t = _txn(db, name="Unknown POS 4471", memo="", vendor_id=None)
        db.commit()

        n = _attribute(db, [t.id])
        db.refresh(t)
        assert n == 0, n
        assert t.attribution_status == "unattributed", t.attribution_status
        assert t.project_id is None, t.project_id
    finally:
        db.close()


# -- manual attribution is sacrosanct ----------------------------------------

def test_manual_attribution_is_never_overwritten():
    db = _session()
    try:
        p_manual = _project(db, "MAN-1")
        p_rule = _project(db, "RUL-9")
        v = _vendor(db, "Acme", default_project_id=None)
        _rule(db, "acme", p_rule.id)
        # Operator manually pinned this txn to p_manual. A rule would otherwise
        # send it to p_rule, but a manual decision must be preserved.
        t = _txn(db, name="Acme", memo="", vendor_id=v.id,
                 status="attributed", method="manual", project_id=p_manual.id)
        db.commit()

        n = _attribute(db, [t.id])
        db.refresh(t)
        assert t.project_id == p_manual.id, t.project_id
        assert t.attribution_method == "manual", t.attribution_method
        assert n == 0, "manual rows must not be counted as (re)attributed"
    finally:
        db.close()


# -- default scope only touches unattributed rows ----------------------------

def test_default_scope_skips_already_attributed_rows():
    db = _session()
    try:
        p = _project(db, "SCOPE-1")
        v = _vendor(db, "Acme", default_project_id=p.id)
        t_new = _txn(db, name="Acme", memo="", vendor_id=v.id, status="unattributed")
        t_done = _txn(db, name="Acme", memo="", vendor_id=v.id,
                      status="attributed", method="vendor_default", project_id=p.id)
        db.commit()

        # No txn_ids -> only rows still marked "unattributed" are scanned.
        n = _attribute(db, None)
        db.refresh(t_new)
        db.refresh(t_done)
        assert n == 1, n
        assert t_new.attribution_status == "attributed", t_new.attribution_status
        assert t_new.project_id == p.id, t_new.project_id
        # The already-attributed row was never rescanned.
        assert t_done.attribution_method == "vendor_default", t_done.attribution_method
    finally:
        db.close()


def _run():
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS %s" % name)
            except AssertionError as e:
                failures += 1
                print("FAIL %s: %s" % (name, e))
            except Exception as e:  # noqa: BLE001
                failures += 1
                print("ERROR %s: %s" % (name, e))
    if failures:
        print("\n%d test(s) failed." % failures)
        sys.exit(1)
    print("\nAll attribution tests passed.")


if __name__ == "__main__":
    _run()
