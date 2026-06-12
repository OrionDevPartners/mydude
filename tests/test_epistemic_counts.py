"""Tests for accurate epistemic-label counting.

The per-run epistemic counts that power the governance trend charts used to be
computed with naive substring matching over the claim-ledger text
(``claim_ledger_text.lower().count(label)``).  That over-counted: "unverified"
contains "verified", and any label word appearing in claim prose inflated the
tally.  ``count_epistemic_labels`` instead parses the structured per-claim
ledger entries (``[CLM-NNN] <label> c=...``) so only the real epistemic label
of each claim is counted.

Runnable two ways:
  * ``python tests/test_epistemic_counts.py``  (standalone, exits non-zero on failure)
  * ``pytest tests/test_epistemic_counts.py``   (test_* functions; no plugins needed)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.swarm.constitution import EpistemicCategory, count_epistemic_labels

ALL_LABELS = {cat.value for cat in EpistemicCategory}


def test_counts_structured_label_not_substring():
    # "unverified" / "derived from" appear in prose; only the structured label
    # token after each [CLM-NNN] should be tallied.
    ledger = (
        "[CLM-001] verified c=0.95: this is clearly an unknown architecture decision\n"
        "[CLM-002] unknown c=0.30: we are unverified about this\n"
        "[CLM-003] hypothesis c=0.50: derived from unverified premises\n"
        "[CLM-004] verified c=0.80: cost analysis"
    )
    counts = count_epistemic_labels(ledger)
    assert counts == {
        "verified": 2,
        "derived": 0,
        "hypothesis": 1,
        "unknown": 1,
    }, counts


def test_substring_method_would_overcount():
    # Guard the regression: the old approach inflates "verified" via "unverified".
    ledger = (
        "[CLM-001] verified c=0.95: we were unverified earlier\n"
        "[CLM-002] unknown c=0.30: unknown territory"
    )
    old_verified = ledger.lower().count("verified")
    new_counts = count_epistemic_labels(ledger)
    assert old_verified == 2, old_verified  # "verified" + "unverified"
    assert new_counts["verified"] == 1, new_counts  # only the real verified claim


def test_shape_is_stable_for_empty_and_nonledger_input():
    # Trend charts depend on a complete {label: count} dict every time.
    for raw in ("", None, "No claims recorded", "free-form prose with no claims"):
        counts = count_epistemic_labels(raw)
        assert set(counts.keys()) == ALL_LABELS, counts
        assert all(v == 0 for v in counts.values()), counts


def test_unknown_label_tokens_are_ignored():
    # A malformed/foreign label token must not be counted as any category.
    ledger = (
        "[CLM-001] bogus c=0.5: not a real category\n"
        "[CLM-002] derived c=0.7: real one"
    )
    counts = count_epistemic_labels(ledger)
    assert counts["derived"] == 1, counts
    assert sum(counts.values()) == 1, counts


def test_confidence_separator_required():
    # Lines that merely start with [CLM-..] but lack the "c=" confidence marker
    # are not structured ledger entries and must not be counted.
    ledger = "[CLM-001] verified some prose without confidence marker"
    counts = count_epistemic_labels(ledger)
    assert sum(counts.values()) == 0, counts


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("ok   %s" % fn.__name__)
        except Exception as e:
            failed += 1
            print("FAIL %s: %s: %s" % (fn.__name__, type(e).__name__, e))
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
