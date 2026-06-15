"""Monthly-normalised spend summary for tracked subscriptions.

Confirmed subscriptions carry an ``est_cost`` string such as ``"$9.99/mo"``,
``"$59.99/yr"`` or ``"$2.99/wk"`` (see :mod:`src.subscriptions.discovery`).
This module totals those into a single "you're spending ~$X/month" figure.

Honesty rules (no fake numbers):
- Only ``confirmed`` rows are counted (candidate/dismissed/cancelled excluded).
- Yearly and weekly costs are normalised to a monthly figure.
- A bare amount with no cadence suffix is treated as monthly, matching the
  "Monthly cost" input on the add form.
- Rows whose cost can't be parsed are excluded from the total and reported as
  ``unknown`` so the headline figure never includes fabricated values.
- Mixed currencies are kept separate (one total per currency symbol) rather
  than summed into a meaningless number.
"""

import re

# Amount with a currency symbol, e.g. "$12.99", "€9", "£14.50". Mirrors the
# symbols normalised by src.subscriptions.discovery._extract_amount.
_AMOUNT_RE = re.compile(r"(?P<sym>[$€£])\s?(?P<num>[0-9][0-9,]*(?:\.[0-9]+)?)")

# Cadence suffix -> multiplier to convert one period into a monthly figure.
_CADENCE_FACTOR = (
    ("/mo", 1.0),
    ("/yr", 1.0 / 12.0),
    ("/wk", 52.0 / 12.0),
)


def parse_monthly(est_cost):
    """Parse an ``est_cost`` string into ``(currency_symbol, monthly_amount)``.

    Returns ``None`` when no amount can be parsed (an honest "unknown"). Yearly
    and weekly costs are normalised to a monthly figure; a bare amount with no
    recognised cadence suffix is treated as monthly.
    """
    if not est_cost:
        return None
    m = _AMOUNT_RE.search(est_cost)
    if not m:
        return None
    try:
        amount = float(m.group("num").replace(",", ""))
    except ValueError:
        return None
    sym = m.group("sym")
    factor = 1.0
    tail = est_cost.rstrip().lower()
    for suffix, f in _CADENCE_FACTOR:
        if tail.endswith(suffix):
            factor = f
            break
    return sym, amount * factor


def summarize_monthly_spend(rows):
    """Total confirmed subscriptions into a monthly-normalised spend summary.

    ``rows`` is the serialized subscription list (dicts with ``status`` and
    ``est_cost``). Returns a dict with one ``monthly_total`` per currency, the
    number of rows ``counted`` and the number excluded as ``unknown``.
    """
    totals = {}
    counted = 0
    unknown = 0
    for r in rows:
        if r.get("status") != "confirmed":
            continue
        parsed = parse_monthly(r.get("est_cost"))
        if parsed is None:
            unknown += 1
            continue
        sym, monthly = parsed
        totals[sym] = totals.get(sym, 0.0) + monthly
        counted += 1
    currencies = [
        {"currency": sym, "monthly_total": round(amt, 2)}
        for sym, amt in sorted(totals.items(), key=lambda kv: -kv[1])
    ]
    return {"currencies": currencies, "counted": counted, "unknown": unknown}
