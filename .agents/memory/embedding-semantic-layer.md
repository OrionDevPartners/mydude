---
name: Embedding semantic layer
description: How the optional real-embedding backend layers onto the TF-IDF recall/contradiction stack, and the one design gotcha.
---

# Optional vector-embedding layer

A provider-agnostic embedding capability sits *above* the lexical TF-IDF
similarity used in recall and contradiction detection. It is OPTIONAL: when no
backend resolves, every consumer degrades to TF-IDF and never raises.

**Resolution / gating:** candidates are tried in order — env vars, then the
local model registry (`kind: embedding` entries), then **vault-keyed defaults**:
a keyed cloud API (`OPENAI_API_KEY` → `text-embedding-3-small`, `GEMINI_API_KEY`
→ `text-embedding-004` via Gemini's OpenAI-compat endpoint) and finally an
always-appended local `fastembed` (`BAAI/bge-small-en-v1.5`) so semantic routing
works with no key and no cloud egress. Resolution honors the `cloud_shift` kill
switch — when cloud egress is off only `exec_locus=local` backends are eligible.
Backends are cached with a short TTL; helpers return `None`/`[]` for TF-IDF.

**Probe-validate at resolve time (critical):** `is_available()` only proves a
client built (key present) — a credentialed-but-broken cloud endpoint passes it
yet errors on every `embed()`, which would silently pin the system to TF-IDF
*and never try the local fallback*. So `_resolve()` runs one real `embed(["ok"])`
probe and skips any candidate that fails it, falling through to fastembed.
**Why:** in this workspace Gemini's OpenAI-compat `/embeddings` returns HTTP 500
for every model id (`text-embedding-004`, `gemini-embedding-001`, …) — the probe
is what lets a working local fastembed take over instead of staying broken.
Note `sentence-transformers` is installed-but-broken here; fastembed 0.8.0 works.

**Consumers:** KG `semantic_search`, `LocalMemoryAdapter` cache ranking, and
`ConsistencyChecker` similarity all prefer embedding cosine when available.

## Gotcha: embeddings only raise the *similarity gate*, they don't create contradictions
The negation-proximity contradiction check still requires **lexical** keyword
overlap between the claim's negation window and the fact's keywords. Embeddings
raise the `sim` score that *gates* that check (and the KG `min_score` gate), but
a pure paraphrase contradiction with no shared words and no temporal/negation
token will still not fire.

**Why:** the win embeddings unlock is cases like "finish by Friday" vs "deadline
is Monday" — zero shared content words, so TF-IDF `min_score` never surfaced
them; embedding cosine clears the gate and the temporal-conflict rule then flags
it. So the value is recall + clearing similarity gates, not a new contradiction
signal.

**How to apply:** when extending contradiction logic, don't assume high
embedding similarity alone yields a flagged conflict — a negation/temporal/
semantic-opposition signal is still required on top.
