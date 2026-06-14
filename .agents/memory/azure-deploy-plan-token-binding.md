---
name: Deploy plan-token payload binding
description: What a signed approval token for a billable/irreversible IaC apply must bind, and why partial binding is unsafe.
---

# Two-phase deploy approval tokens must bind the COMPLETE payload

When a "plan" phase mints a short-lived signed token that later authorizes a
billable, irreversible apply (e.g. ARM `begin_create_or_update`), the token must
fingerprint the **entire** payload the apply is allowed to submit — not just
*which* resources change.

Bind ALL THREE, required at BOTH mint time and apply time (fail loud if any is
missing/empty — never treat one as optional):
1. **plan_hash** — the what-if change set, and it must include a **sanitized
   property delta** per change (recursive, sorted, `{path, property_change_type}`
   only — NEVER before/after values). Without the delta, a same-resource /
   different-property change reuses the old `{change_type, resource_id}` hash and
   rides a stale token.
2. **template_hash** — the exact compiled template. Two different templates can
   produce the same change-type/resource-id set while applying different property
   values, so the change set alone does not pin effects.
3. **params_hash** — parameters carry deployment effects AND secrets not fully
   represented by the template or the value-free delta.

**Why:** Architect failed this task twice. First the token bound only
`{change_type, resource_id}`; then template/params binding was added but params
was still optional and the success output echoed `params_hash`. A partial binding
lets a drifted or unpinned payload reach the irreversible action.

**How to apply:**
- Re-validate all three against a LIVE what-if immediately before submit; refuse
  ("drift") if any differs. Missing binding OR any drift must never reach the
  destructive call.
- Never echo the hashes back to the caller — they are fingerprints of
  secret-bearing inputs; keep them bound INSIDE the token only.
- Pair this with a guaranteed-before-action strict audit (refuse if the durable
  audit record can't be written) — see the governed-degraded-fallback / audit
  rules.
