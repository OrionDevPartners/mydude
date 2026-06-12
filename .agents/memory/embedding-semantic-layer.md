---
name: Embedding semantic layer
description: How the optional real-embedding backend layers onto the TF-IDF recall/contradiction stack, and the one design gotcha.
---

# Optional vector-embedding layer

A provider-agnostic embedding capability sits *above* the lexical TF-IDF
similarity used in recall and contradiction detection. It is OPTIONAL: when no
backend resolves, every consumer degrades to TF-IDF and never raises.

**Resolution / gating:** backends are resolved from env vars first, then the
local model registry (`kind: embedding` entries). Resolution honors the
`cloud_shift` kill switch — when cloud egress is off, only `exec_locus=local`
backends (in-process sentence-transformers, or a local Ollama/MLX `/embeddings`
server) are eligible, so embeddings stay sovereign. Backends are cached with a
short TTL; helpers return `None`/`[]` to signal the TF-IDF fallback.

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
