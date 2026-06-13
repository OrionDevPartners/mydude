---
name: Governance participation floor
description: Why governance auto-resolution gates BOTH enact and reject behind a configurable participation floor, and how the floor is resolved.
---

# Governance participation floor

Auto-resolution in `GovernanceEngine._maybe_enact` must gate BOTH the enact branch
AND the reject branch behind a minimum participation floor, checked BEFORE the
yes/no ratio test. Otherwise the first single vote is 100% of the (tiny) tally and
instantly decides — a quorum on a ratio alone is meaningless without a floor on how
many actually voted.

**Two dimensions:** distinct voters (`min_voters`, default 2) and total effective
vote weight (`min_weight`, default 0.0 = disabled). Both must be met. A floor of
`min_voters=1` deliberately restores the legacy single-vote enact.

**Why fail-SAFE (fall back to default), not fail-loud, on malformed env:** the
floor is read live per-resolution on the vote-casting hot path. A malformed
`GOVERNANCE_MIN_VOTERS` must NOT crash `cast_vote`; falling back to the safer
built-in default keeps voting working while preserving protection. Negatives clamp
to 0 (dimension disabled). This is the one place we chose fail-safe over the HARD
fail-loud pillar — because crashing the vote path is the *less* safe outcome.

**Config (live env, mirrors the error_metrics pattern):** global
`GOVERNANCE_MIN_VOTERS` / `GOVERNANCE_MIN_WEIGHT`, with per-track overrides
`..._TUNING` / `..._POLICY` / `..._SAFETY` that win over the global for that track.
Known gap: a per-track typo silently inherits the global value (only logged).

**Abstain:** counts toward `participation_weight` and `vote_count` (it IS
participation) but never toward `total_effective` (not a yes/no decision), so an
all-abstain proposal meets the floor yet stays open.

**How to apply:** any new auto-resolution branch (new track or decision rule) must
run the same `participation_status(tally, track)["participation_met"]` gate before
deciding. Live UI is the SPA Proposals tab fed by `/api/governance`; there is
intentionally NO quorum-vote HTTP endpoint (only the audited operator_enact/reject
override) — don't add one without governance review.
