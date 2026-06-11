---
name: Voice/prosody mood capture
description: Governance constraints unique to the Hume audio (prosody) mood path vs the text path.
---

# Voice mood capture (Hume prosody)

The coach's voice/audio mood path goes through Hume's `prosody` model (batch job,
multipart file upload), distinct from the `language` model used for typed text.

Two non-obvious, durable constraints:

1. **Prosody emits NO sentiment scale**, so there is no honest valence/arousal to
   derive. The normalized result reports `valence=None` (and `arousal=None`)
   rather than a computed/fabricated number.
   **Why:** governance pillar #1 (no fabrication). A derived valence from an
   emotion lexicon would be invented data. Consequence: voice signals don't show
   on the valence mood-trend chart — that is intentional, not a bug.

2. **Audio ALWAYS uses the Hume cloud** — there is no on-device prosody model.
   So strict-private mode must REFUSE the voice path unconditionally (fail loud),
   unlike text where strict-private can fall back to a local-pinned LLM sentiment.

**How to apply:** if a future provider/local prosody model is added, only then can
strict-private voice capture be allowed; until then the refusal is required.
