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
