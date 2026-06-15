"""Tests for the governed MCP knowledge-siphon (Task #219).

The siphon distills every successful MCP interaction into a COMPACT, non-secret
memory claim so the brain self-improves from its own headless use. These tests
run completely offline against a recording fake substrate — no DB, no network,
no real swarm. They assert the governance invariants:

  * ``memory_*`` capabilities and failed interactions are never siphoned (no
    recall -> write loop; only successes are learned from).
  * Governed completions are admitted ONLY when the swarm's own compliance /
    hallucination scores clear the bar; ungoverned / sub-threshold output is
    skipped, never stored raw (pillar #4).
  * Read / deploy tools store a compact summary only — never raw rows, SQL,
    query literals, params, prompts (beyond a short excerpt), or plan tokens.
  * Contradicted siphons are kept (provenance) but down-weighted + flagged.
  * The write is fail-soft + audited; it never raises into the request path.
  * The integration recall projection never returns private entries or metadata.

Runnable two ways:
  * ``python tests/test_mcp_memory_siphon.py``   (standalone; exits non-zero on failure)
  * ``pytest tests/test_mcp_memory_siphon.py``    (test_* functions; no plugins needed)
"""
import asyncio
import datetime as _dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.memory.siphon as siphon


# -- fakes --------------------------------------------------------------------

class _FakeEntry:
    def __init__(self, memory_id, content, category, confidence, source,
                 verified, metadata, created_at=None):
        self.memory_id = memory_id
        self.content = content
        self.category = category
        self.confidence = confidence
        self.source = source
        self.verified = verified
        self.metadata = metadata
        self.created_at = created_at


class _FakeSubstrate:
    """Records write_claim calls; returns canned contradictions / recall hits."""

    def __init__(self, contradictions=None, recall_results=None):
        self.written = []
        self._contradictions = contradictions or []
        self._recall = recall_results or []
        self.recall_args = None

    def find_contradictions(self, claim, threshold=0.25):
        return self._contradictions

    def write_claim(self, content, category, confidence, source, verified,
                    metadata=None, local_only=False):
        entry = _FakeEntry(
            memory_id="m-%d" % (len(self.written) + 1),
            content=content, category=category, confidence=confidence,
            source=source, verified=verified, metadata=metadata or {},
        )
        self.written.append(entry)
        return entry

    def recall(self, query, top_k=5, category=None, min_confidence=0.3):
        self.recall_args = dict(query=query, top_k=top_k, category=category,
                                min_confidence=min_confidence)
        return list(self._recall)


class _BoomSubstrate:
    def find_contradictions(self, *a, **k):
        return []

    def write_claim(self, *a, **k):
        raise RuntimeError("db down")


def _governed(compliance_score, hr_avg, synthesis="The governed answer."):
    return {"ok": True, "result": {
        "SYNTHESIS": synthesis,
        "COMPLIANCE_SCORES": [{"score": compliance_score}],
        "HALLUCINATION_RISK": {"average": hr_avg},
    }}


class _patch_audit:
    """Patch integrations.audit_capability so fail-soft audits don't touch the DB."""

    def __init__(self):
        self.calls = []
        self._prev = None

    def __enter__(self):
        import src.swarm.integrations as INTEG
        self._mod = INTEG
        self._prev = INTEG.audit_capability
        INTEG.audit_capability = lambda *a, **k: self.calls.append((a, k))
        return self

    def __exit__(self, *exc):
        self._mod.audit_capability = self._prev
        return False


# -- exclusions ---------------------------------------------------------------

def test_build_claim_excludes_memory_capabilities():
    assert siphon.build_siphon_claim("memory_recall", {}, {"ok": True}) is None
    assert siphon.build_siphon_claim("memory_write", {}, {"ok": True}) is None


def test_build_claim_skips_failed_and_nondict_interactions():
    assert siphon.build_siphon_claim("azure_pg_select", {}, {"ok": False}) is None
    assert siphon.build_siphon_claim("azure_pg_select", {}, None) is None
    assert siphon.build_siphon_claim("", {}, {"ok": True}) is None


# -- governed-completion gate -------------------------------------------------

def test_governed_completion_passes_threshold_and_bounds_fields():
    cand = siphon.build_siphon_claim(
        "azure_aoai_complete", {"prompt": "p" * 500}, _governed(90, 0.1, "x" * 5000))
    assert cand is not None
    assert cand["category"] == siphon.SIPHON_CATEGORY
    assert cand["confidence"] == 0.9
    assert len(cand["content"]) == 1000  # SYNTHESIS truncated
    assert len(cand["metadata"]["prompt_excerpt"]) == 200  # prompt excerpt bounded
    assert cand["metadata"]["kind"] == "governed_completion"
    assert cand["metadata"]["compliance"] == 0.9
    assert cand["metadata"]["hallucination_risk"] == 0.1


def test_governed_completion_blocked_below_compliance():
    assert siphon.build_siphon_claim(
        "azure_aoai_complete", {}, _governed(50, 0.1)) is None


def test_governed_completion_blocked_above_hallucination():
    assert siphon.build_siphon_claim(
        "azure_aoai_complete", {}, _governed(95, 0.5)) is None


def test_ungoverned_completion_is_never_siphoned():
    # No COMPLIANCE_SCORES / HALLUCINATION_RISK -> ungoverned -> fail closed.
    assert siphon.build_siphon_claim(
        "azure_aoai_complete", {}, {"ok": True, "result": {"SYNTHESIS": "x"}}) is None
    # No governed envelope at all.
    assert siphon.build_siphon_claim(
        "azure_aoai_complete", {}, {"ok": True}) is None
    # Empty synthesis even when scored.
    assert siphon.build_siphon_claim(
        "azure_aoai_complete", {}, _governed(95, 0.05, synthesis="   ")) is None


# -- read / deploy summaries: no raw data leakage -----------------------------

def test_pg_select_summary_never_leaks_rows_or_sql():
    params = {"db_key": "main", "sql": "SELECT secret_col FROM users WHERE x=1"}
    data = {"ok": True, "rows": [["topsecretvalue"]], "rowcount": 1,
            "columns": ["secret_col"], "truncated": False}
    cand = siphon.build_siphon_claim("azure_pg_select", params, data)
    blob = json.dumps(cand)
    assert "topsecretvalue" not in blob  # no raw row values
    assert params["sql"] not in blob  # no raw SQL literal
    assert "secret_col" not in blob  # no column names or query identifiers
    assert "users" not in blob
    assert cand["metadata"]["rowcount"] == 1
    assert cand["metadata"]["column_count"] == 1


def test_cosmos_summary_is_compact():
    cand = siphon.build_siphon_claim(
        "azure_cosmos_read",
        {"database": "db", "container": "c", "query": "SELECT * FROM c"},
        {"ok": True, "items": [1, 2, 3], "count": 3})
    assert "SELECT" not in json.dumps(cand)
    assert cand["metadata"]["count"] == 3
    assert "3 item" in cand["content"]


def test_deploy_plan_never_leaks_token_or_hash():
    data = {"ok": True, "change_count": 4, "plan_token": "SECRETTOKEN",
            "plan_hash": "DEADBEEF", "confirm": "APPLY AZURE DEPLOYMENT"}
    cand = siphon.build_siphon_claim("azure_deploy_plan", {}, data)
    blob = json.dumps(cand)
    assert "SECRETTOKEN" not in blob and "DEADBEEF" not in blob
    assert "APPLY AZURE DEPLOYMENT" not in blob
    assert cand["metadata"]["change_count"] == 4


def test_unknown_capability_gets_generic_provenance_only():
    cand = siphon.build_siphon_claim(
        "future_tool_v2", {"secret": "x"}, {"ok": True, "weird": "shape", "k": 9})
    assert cand is not None
    assert cand["metadata"]["kind"] == "generic"
    assert "shape" not in json.dumps(cand) and "secret" not in json.dumps(cand)


# -- siphon_interaction: persistence, contradictions, fail-soft ---------------

def test_siphon_interaction_writes_unverified_governed_completion():
    sub = _FakeSubstrate()
    mid = siphon.siphon_interaction(
        "azure_aoai_complete", {"prompt": "q"}, _governed(90, 0.1), substrate=sub)
    assert mid == "m-1"
    assert len(sub.written) == 1
    w = sub.written[0]
    assert w.verified is False
    assert w.source == "mcp:azure_aoai_complete"
    assert w.category == siphon.SIPHON_CATEGORY
    assert w.metadata.get("contradicted") is None


def test_contradiction_caps_confidence_and_flags():
    sub = _FakeSubstrate(contradictions=[{"id": "c1"}, {"id": "c2"}])
    siphon.siphon_interaction(
        "azure_aoai_complete", {"prompt": "q"}, _governed(95, 0.05), substrate=sub)
    w = sub.written[0]
    assert w.confidence <= 0.3
    assert w.metadata["contradicted"] is True
    assert w.metadata["contradiction_count"] == 2


def test_siphon_interaction_skips_excluded_without_writing():
    sub = _FakeSubstrate()
    assert siphon.siphon_interaction("memory_recall", {}, {"ok": True}, substrate=sub) is None
    assert siphon.siphon_interaction("azure_pg_select", {}, {"ok": False}, substrate=sub) is None
    assert sub.written == []


def test_siphon_interaction_never_raises_on_substrate_error():
    with _patch_audit() as audit:
        out = siphon.siphon_interaction(
            "azure_pg_select", {"db_key": "m"}, {"ok": True, "rowcount": 1},
            substrate=_BoomSubstrate())
    assert out is None  # swallowed, never raised
    assert audit.calls, "a failed siphon must be audited"
    assert audit.calls[0][0][0] == "mcp_memory_siphon"
    assert audit.calls[0][1].get("status") == "error"


# -- integration recall projection: no private entries, no metadata -----------

def test_integration_memory_recall_filters_private_and_drops_metadata():
    import src.memory.substrate as SUB
    import src.swarm.integrations as INTEG
    entries = [
        _FakeEntry("a", "public fact", "mcp_interaction", 0.8, "mcp:x", False,
                   {"capability": "azure_pg_select"},
                   created_at=_dt.datetime(2026, 6, 15, 12, 0, 0)),
        _FakeEntry("b", "private twin secret", "digital_twin", 0.9, "twin", False,
                   {"private": True}),
    ]
    sub = _FakeSubstrate(recall_results=entries)
    prev = SUB.get_substrate
    SUB.get_substrate = lambda: sub
    try:
        with _patch_audit():
            out = asyncio.run(INTEG.Integrations().memory_recall(
                {"query": "q", "top_k": 5, "source": "mcp:memory_recall"}))
    finally:
        SUB.get_substrate = prev
    data = json.loads(out)
    assert data["ok"] is True
    assert data["count"] == 1
    assert data["private_filtered_count"] == 1
    row = data["results"][0]
    assert row["memory_id"] == "a"
    assert "metadata" not in row  # arbitrary metadata is never exposed
    assert "private twin secret" not in out
    assert row["created_at"] == "2026-06-15T12:00:00"


def test_integration_memory_recall_requires_query():
    import src.swarm.integrations as INTEG
    with _patch_audit():
        out = asyncio.run(INTEG.Integrations().memory_recall({"source": "mcp:memory_recall"}))
    data = json.loads(out)
    assert data["ok"] is False and "query" in data["error"]


def _run_all():
    tests = [
        test_build_claim_excludes_memory_capabilities,
        test_build_claim_skips_failed_and_nondict_interactions,
        test_governed_completion_passes_threshold_and_bounds_fields,
        test_governed_completion_blocked_below_compliance,
        test_governed_completion_blocked_above_hallucination,
        test_ungoverned_completion_is_never_siphoned,
        test_pg_select_summary_never_leaks_rows_or_sql,
        test_cosmos_summary_is_compact,
        test_deploy_plan_never_leaks_token_or_hash,
        test_unknown_capability_gets_generic_provenance_only,
        test_siphon_interaction_writes_unverified_governed_completion,
        test_contradiction_caps_confidence_and_flags,
        test_siphon_interaction_skips_excluded_without_writing,
        test_siphon_interaction_never_raises_on_substrate_error,
        test_integration_memory_recall_filters_private_and_drops_metadata,
        test_integration_memory_recall_requires_query,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print("PASS", t.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL", t.__name__, "->", e)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("ERROR", t.__name__, "->", type(e).__name__, e)
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
