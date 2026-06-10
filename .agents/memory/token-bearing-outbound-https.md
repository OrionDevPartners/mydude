---
name: token-bearing-outbound-https
description: Operator-supplied outbound URLs that carry a bearer token/secret must enforce https:// in code, not just in docs.
---

# Token-bearing outbound URLs must enforce HTTPS in code

When an operator-configured URL is used to send a bearer token or any secret
(e.g. `AVATAR_BRIDGE_URL` + `AVATAR_BRIDGE_TOKEN` in the avatar bridge), the
"https only" rule must be **enforced in code and fail loud**, not merely stated in
a docstring/spec.

**Why:** A documented invariant ("only negotiates over HTTPS") is worthless if
unenforced — an `http://` value silently sends the bearer token in plaintext. This
violates governance pillar #3 (provider/secret separation) and #1 (fail-loud).

**How to apply:** Enforce at BOTH layers so the gap can't be reached:
- config/status + `*_configured()` helpers treat a non-https URL as **not
  configured** (so the UI shows it honestly and the session layer degrades / fails
  loud instead of using it); and
- the actual request function re-checks and raises (defense in depth) before
  attaching the Authorization header.
Add a test that a plaintext URL is refused and never negotiated.
