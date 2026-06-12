---
name: Two-phase irreversible-action confirmation gate
description: Why the cancel confirmation gate is decoupled from the best-effort review login in MyDude's subscription/automation flows.
---

# Two-phase confirmation gate for irreversible browser actions

For any irreversible governed browser action (e.g. cancelling a subscription),
the explicit-confirmation gate (user must type a literal word like `CANCEL`) is
the safety mechanism and must be **decoupled** from whether the best-effort
"review login" succeeded.

**Rule:** Requesting the irreversible action (phase 1) always moves the record to
a `*_pending` status and always surfaces the confirmation gate, regardless of
whether the review login was blocked, needed-user, or errored. The login outcome
is reported honestly alongside the gate. Phase 2 (the irreversible step)
re-checks policy and refuses unless the record is in the `*_pending` state.

**Why:** An earlier version returned early when the review login was policy-blocked
(e.g. `ENABLE_BROWSER_CAPABILITY` off), so the confirmation gate never rendered.
That made the safety-critical gate invisible and untestable in any environment
where the browser capability can't run (which is the default, and the local
container can't launch Chromium). Decoupling keeps the gate always-visible and
testable while staying honest about capability status. Safety is preserved
because the irreversible step independently enforces both the pending status and
the policy gate.

**How to apply:** When adding new irreversible automation actions, mirror this:
phase-1 marks intent + shows the gate + honest status; phase-2 enforces
pending-state + policy before acting. Never gate the *confirmation UI* behind a
best-effort capability call.

## Cancel-walk success must mean *confirmed*, not *clicked something*

The automated cancel walker (`_do_cancel` in `src/browser/backends.py`) must not
report success merely because it clicked one or more controls. A retention screen
can offer ONLY keep/pause/stay/offer controls (no decline path); the walker
correctly refuses to click them, but the subscription is still active behind the
wall.

**Rule:** `_do_cancel` returns `ok=True` only when it reached a terminal
*confirmation* control (`_looks_like_confirm`). If it stalls on a screen whose
only remaining controls are retention/keep (`_has_visible_keep_control`) without
confirming, it returns `ok=False` with a needs-you message containing the word
"yourself" (so `_finish_interactive` classifies it `needs_user`). That keeps the
record in `cancel_pending` (confirm_cancel only flips to `cancelled` when the
output starts with "browser_cancel ok"), so the user can retry/finish by hand.

**Why:** detecting keep-only screens needs a *separate* scan by retention labels
(`RETENTION_KEEP_TEXTS`), because keep controls don't contain cancel-label
substrings — the cancel-label finder never sees them, so it can't tell "retention
wall" from "no cancel control here".
