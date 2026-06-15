---
name: MCP knowledge siphon
description: Governance invariants for distilling MCP interactions into long-term memory + the read-only recall projection.
---

# MCP knowledge siphon (self-improving brain)

The owner-only MCP server both READS long-term governed memory (a `memory_recall`
tool) and WRITES back into it: every *successful* tool interaction is distilled
into a compact memory claim so the brain learns from its own headless use. The
siphon is purely additive — it must never alter a tool's result and must never
raise into the request path.

## Hard invariants (do not weaken)
- **Governed-only writes (pillar #4).** A governed-completion interaction is
  siphoned ONLY when the swarm's own scores clear `compliance >= 0.80` AND
  `hallucination_risk <= 0.25`. Ungoverned output (no compliance/HR envelope) and
  sub-threshold output are skipped entirely — never stored raw.
- **Compact, non-secret summaries.** Read/deploy tools store a one-line summary +
  small metadata counts only. Never persist raw rows, SQL/query literals, params,
  full prompts (cap a prompt excerpt at ~200 chars), SYNTHESIS beyond ~1000 chars,
  or plan/confirm tokens. Test by asserting the secret/literal is absent from
  `json.dumps(candidate)`.
- **No recall->write loop.** `memory_*` capabilities and any `ok != True` (failed)
  interaction are excluded, or recall results feed back into memory and failures
  pollute it.
- **Contradictions kept but down-weighted.** If the substrate finds contradictions,
  still persist (provenance) but cap confidence (<=0.3) and flag
  `contradicted/contradiction_count`.
- **Fail-soft + audited.** A substrate error must be swallowed (return None) and
  audited via `integrations.audit_capability("mcp_memory_siphon", ..., status="error")`,
  never propagated — the siphon runs off the hot path (asyncio.to_thread) and an
  env kill switch disables it. Writes are `verified=False`, `source="mcp:<cap>"`.
- **Recall projection is filtered.** `integrations.memory_recall` drops entries with
  `metadata['private']=True`, strips arbitrary metadata, returns only safe fields +
  a `private_filtered_count`.

**Why:** these mirror MyDude's governance pillars — the memory is the brain's
long-term state, so ungoverned/raw/secret content reaching it is as bad as it
reaching a user; a siphon that raises would turn a learning nicety into a
request-path regression.

**How to apply:** when extending the siphon to a new capability, add a compact
builder branch (summary + counts only), confirm it's excluded if read-back/secret,
and keep the governed-completion gate as the only path that stores model text.
Hermetic tests use a recording fake substrate; patch `audit_capability` to avoid
DB writes and `src.memory.substrate.get_substrate` for the recall projection.
