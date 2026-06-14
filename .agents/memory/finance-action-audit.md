---
name: Governed action endpoints must audit failure paths
description: Finance/Plaid connection endpoints must write an audit row for every outcome (success AND every failure), with secret-free detail.
---

# Governed action endpoints audit every outcome, not just success

For governed outbound/connection actions (e.g. the Plaid Link endpoints:
link-token create, public_token exchange, item remove), every attempt must write
a `FinanceAuditLog` row — on success **and** on every failure branch
(not-configured / auth error / provider error / prod encryption-guard refusal).

**Why:** Governance pillar 4 ("no ungoverned action") means the accountability
trail must capture *attempts and failures*, not only the happy path. A first pass
audited only successful storage; an architect review flagged the missing
link-token + exchange-failure audit rows as a blocking gap. A failed/blocked bank
connection is exactly the event an auditor needs to see.

**How to apply:**
- Audit detail must be **secret-free** — never put `public_token`, `access_token`,
  `link_token`, or the client secret in the `detail` string; log error codes /
  messages / item metadata only.
- In async endpoints, the audit write is a blocking DB op — run it via
  `asyncio.to_thread` (own short-lived session; swallow audit-write errors so
  auditing never masks the real outcome).
- Mirror the pattern when adding the next provider (QuickBooks, etc.): wire the
  audit row into each `except` branch as you write the endpoint, not afterward.
