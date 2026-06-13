"""Tests for the semantic-reasoning + recursive-memory system.

These guard the end-to-end behaviour described in the platform spec:
  * TF-IDF cosine catches semantically related claims that the old Jaccard
    keyword overlap would miss (it falls below the contradiction gate).
  * Temporal-conflict detection fires for "deadline Monday" vs "finish Friday"
    style pairs but stays quiet for unrelated sentences.
  * Facts written in one task session are recalled in a *subsequent* one
    (durable cross-session memory via the persisted local KG).
  * The bidirectional Cognee<->Mem0 bridge is idempotent and never downgrades
    a VERIFIED entry during a merge.
  * The RECURSIVE_REASONER cognitive role is scheduled only in the deep waves.

It also pins the re-entrant-lock regression in the vendored KnowledgeGraph:
``add_edge`` calls ``add_node`` while holding ``_LOCK``, so a non-reentrant
lock deadlocks whenever a relation's endpoint nodes don't already exist —
which silently hangs ``write_claim`` for ordinary prose.

Hermetic: COGNEE_DATA_DIR / MEM0_DATA_DIR are redirected to a throwaway temp
dir *before* any memory module is imported, so the repo's real .cognee_data /
.mem0_data stores are never touched and every run starts from empty.

Runnable two ways:
  * ``python tests/test_semantic_memory.py``  (standalone, exits non-zero on failure)
  * ``pytest tests/test_semantic_memory.py``   (test_* functions; no plugins needed)
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Redirect both stores to a temp dir BEFORE importing anything that binds the
# module-level data paths (graph.py / mem0 store.py read these at import time).
_TMP = tempfile.mkdtemp(prefix="memtest_")
os.environ["COGNEE_DATA_DIR"] = os.path.join(_TMP, "cognee")
os.environ["MEM0_DATA_DIR"] = os.path.join(_TMP, "mem0")
os.environ.pop("MEM0_API_KEY", None)  # force the self-contained local-file mode

from src.swarm.provenance import (  # noqa: E402
    ConsistencyChecker,
    _extract_keywords,
    _jaccard_similarity,
    _temporal_conflict,
    _tfidf_cosine,
)
from src.swarm.contract import (  # noqa: E402
    CognitiveRole,
    ROLE_BASE_WEIGHTS,
    get_role_prompt_suffix,
    map_wave_to_cognitive_roles,
)
from src.memory.adapter import MemoryEntry  # noqa: E402
from src.memory.bridge import MemoryBridge, _merge_entries  # noqa: E402
from src.memory.cloud_store import CloudMemoryAdapter  # noqa: E402
from src.memory.local_store import LocalMemoryAdapter  # noqa: E402
from src.memory.substrate import MemorySubstrate  # noqa: E402
from src.vendors.cognee.graph import KnowledgeGraph  # noqa: E402


# The contradiction gate inside ConsistencyChecker: negation is only considered
# when topic similarity exceeds this. Jaccard scoring an obviously-related pair
# below this is exactly the failure mode TF-IDF cosine fixes.
_CONTRADICTION_GATE = 0.12

# A semantically-equivalent contradiction pair. They restate the same claim with
# opposite polarity but share very few literal keywords amid filler prose, so
# Jaccard overlap collapses while TF-IDF cosine (term-weighted) stays meaningful.
_FACT = "Authentication uses bcrypt password hashing"
_NEGATING_CLAIM = (
    "Honestly, after carefully auditing every relevant module throughout our "
    "entire sprawling backend codebase yesterday, the login subsystem does not "
    "employ bcrypt anywhere whatsoever for protecting credentials"
)


# --------------------------------------------------------------------------- #
# 1. TF-IDF cosine catches what Jaccard misses
# --------------------------------------------------------------------------- #
def test_tfidf_beats_jaccard_below_gate():
    # Jaccard drops below the contradiction gate (would miss the conflict);
    # TF-IDF cosine clears it (catches it). This is the whole reason the
    # primary similarity layer was switched away from Jaccard.
    kf = _extract_keywords(_FACT)
    kn = _extract_keywords(_NEGATING_CLAIM)
    jaccard = _jaccard_similarity(kn, kf)
    tfidf = _tfidf_cosine(_NEGATING_CLAIM, _FACT, [_FACT])

    assert jaccard < _CONTRADICTION_GATE, jaccard
    assert tfidf > _CONTRADICTION_GATE, tfidf
    assert tfidf > jaccard, (tfidf, jaccard)


def test_consistency_flags_negation_only_tfidf_can_reach():
    # End-to-end: with Jaccard alone this contradiction is invisible (sim under
    # the gate). The TF-IDF layer lifts the similarity so the negation near the
    # shared keywords is detected.
    checker = ConsistencyChecker()
    checker.add_verified(_FACT, "CLM-1")
    result = checker.check_consistency(_NEGATING_CLAIM)

    assert result.consistent is False, result.details
    assert len(result.conflicting_claims) == 1, result.conflicting_claims
    assert result.conflicting_claims[0]["claim_id"] == "CLM-1"
    assert result.similarity_score > _CONTRADICTION_GATE, result.similarity_score


def test_tfidf_zero_for_unrelated_text():
    a = "The quarterly revenue forecast looks strong"
    b = "Gardening requires patience and good soil"
    assert _tfidf_cosine(a, b, [a, b]) == 0.0


# --------------------------------------------------------------------------- #
# 2. Temporal-conflict detection
# --------------------------------------------------------------------------- #
def test_temporal_conflict_fires_for_deadline_pair():
    assert _temporal_conflict(
        "We must finish the project by Monday",
        "The deadline for the project is Friday",
    ) is True


def test_temporal_conflict_silent_for_unrelated():
    assert _temporal_conflict(
        "The cat sat quietly on the warm mat",
        "Dogs enjoy running around in open parks",
    ) is False


def test_temporal_conflict_same_day_not_a_conflict():
    # Same day named on both sides => agreement, not a conflict.
    assert _temporal_conflict("finish by Monday", "deadline is Monday") is False


def test_temporal_conflict_requires_trigger_words():
    # Different days but no deadline/completion trigger words => not flagged.
    assert _temporal_conflict("Monday was sunny", "Friday was rainy") is False


def test_consistency_detects_temporal_contradiction():
    checker = ConsistencyChecker()
    checker.add_verified("The project deadline is Friday", "CLM-DL")
    result = checker.check_consistency("We will finish the project by Monday")

    assert result.consistent is False, result.details
    reasons = {c["reason"] for c in result.conflicting_claims}
    assert "temporal_conflict" in reasons, result.conflicting_claims


# --------------------------------------------------------------------------- #
# 3. Cross-session recall (durable memory across task sessions)
# --------------------------------------------------------------------------- #
def test_fact_written_in_one_session_recalled_in_next():
    fact = (
        "The customer onboarding flow requires email verification before "
        "dashboard access"
    )
    # Session 1: persist a verified fact, then drop the substrate entirely.
    session_one = MemorySubstrate()
    session_one.write_claim(
        content=fact, category="fact", confidence=0.9, verified=True
    )
    del session_one

    # Session 2: a brand-new substrate restores its local cache from the
    # persisted KG on disk and must surface the prior fact.
    session_two = MemorySubstrate()
    recalled = session_two.recall(
        "email verification onboarding dashboard", top_k=5, min_confidence=0.3
    )

    assert recalled, "expected the prior-session fact to be recalled"
    assert any(
        "onboarding" in e.content and e.verified for e in recalled
    ), [(e.content[:50], e.verified) for e in recalled]


def test_inject_for_task_formats_recalled_memories():
    sub = MemorySubstrate()
    sub.write_claim(
        content="Billing runs on Stripe with monthly invoicing",
        category="decision",
        confidence=0.9,
        verified=True,
    )
    injected = sub.inject_for_task("How does billing work?", top_k=5)
    assert injected, "expected recalled memories to be injected"
    assert any("Stripe" in line for line in injected), injected
    assert any(line.startswith(("[VERIFIED]", "[RECALLED]")) for line in injected)


# --------------------------------------------------------------------------- #
# 4. Bridge: idempotency + VERIFIED preservation
# --------------------------------------------------------------------------- #
def _fresh_bridge():
    local = LocalMemoryAdapter()
    cloud = CloudMemoryAdapter()
    return local, cloud, MemoryBridge(local, cloud)


def test_bridge_is_idempotent():
    local, cloud, bridge = _fresh_bridge()
    local.add(MemoryEntry(memory_id="r1", content="Q3 revenue grew twelve percent", confidence=0.9))

    bridge.sync(direction="both", min_confidence=0.5)
    cloud_after_first = len(cloud.get_all())
    local_after_first = len(local.get_all())

    second = bridge.sync(direction="both", min_confidence=0.5)

    # A converged store: the second run pushes/pulls/merges nothing new.
    assert second.pushed == 0, second.summary()
    assert second.pulled == 0, second.summary()
    assert second.merged == 0, second.summary()
    assert len(cloud.get_all()) == cloud_after_first, "cloud grew on a no-op sync"
    assert len(local.get_all()) == local_after_first, "local grew on a no-op sync"


def test_merge_never_downgrades_verified():
    verified = MemoryEntry(
        memory_id="a", content="same claim", confidence=0.9, verified=True, updated_at=100.0
    )
    # Newer timestamp, lower confidence, NOT verified — must not win away VERIFIED.
    newer_unverified = MemoryEntry(
        memory_id="b", content="same claim", confidence=0.5, verified=False, updated_at=200.0
    )
    merged = _merge_entries(verified, newer_unverified)
    assert merged.verified is True
    assert merged.confidence == 0.9  # max() of both confidences


def test_bridge_pull_keeps_local_verified():
    local, cloud, bridge = _fresh_bridge()
    content = "binding architectural decision alpha"
    local.add(MemoryEntry(memory_id="v", content=content, confidence=0.95, verified=True, updated_at=100.0))
    # Cloud holds a newer but unverified copy of the same content.
    cloud.add(MemoryEntry(memory_id="c", content=content, confidence=0.6, verified=False, updated_at=9_999.0))

    bridge.sync(direction="cloud→local", min_confidence=0.5)

    survivors = [e for e in local.get_all() if e.content == content]
    assert survivors, "verified local entry vanished after pull"
    assert all(e.verified for e in survivors), [(e.memory_id, e.verified) for e in survivors]


def test_bridge_never_egresses_private_entries():
    local, cloud, bridge = _fresh_bridge()
    local.add(MemoryEntry(memory_id="p", content="sensitive emotional private user note", confidence=0.9, metadata={"private": True}))
    local.add(MemoryEntry(memory_id="q", content="public shareable pricing fact", confidence=0.9))

    bridge.sync(direction="local→cloud", min_confidence=0.5)

    cloud_contents = [e.content for e in cloud.get_all()]
    assert "sensitive emotional private user note" not in cloud_contents
    assert "public shareable pricing fact" in cloud_contents


# --------------------------------------------------------------------------- #
# 5. RECURSIVE_REASONER wave scheduling
# --------------------------------------------------------------------------- #
def test_recursive_reasoner_only_in_deep_waves():
    # Early waves (0-2) stay lean; the cross-task graph traversal role is
    # reserved for the deep synthesis waves (>= 3).
    for wave in (0, 1, 2):
        assert CognitiveRole.RECURSIVE_REASONER not in map_wave_to_cognitive_roles(wave), wave
    for wave in (3, 4, 7):
        assert CognitiveRole.RECURSIVE_REASONER in map_wave_to_cognitive_roles(wave), wave


def test_recursive_reasoner_is_governed_role():
    # It must carry a base weight and a role-prompt suffix, or it can't take
    # part in weighted consensus or be dispatched as a real worker.
    assert CognitiveRole.RECURSIVE_REASONER in ROLE_BASE_WEIGHTS
    assert ROLE_BASE_WEIGHTS[CognitiveRole.RECURSIVE_REASONER] > 0
    suffix = get_role_prompt_suffix(CognitiveRole.RECURSIVE_REASONER)
    assert suffix and "memor" in suffix.lower(), suffix


# --------------------------------------------------------------------------- #
# 6. Vendored KnowledgeGraph: search, contradiction, deadlock regression
# --------------------------------------------------------------------------- #
def test_graph_semantic_search_ranks_relevant_node_first():
    graph = KnowledgeGraph()
    graph.add_node("Postgres is the primary datastore", "fact", confidence=0.9)
    graph.add_node("Redis handles ephemeral caching", "fact", confidence=0.9)
    hits = graph.semantic_search("primary datastore postgres", top_k=3)
    assert hits, "semantic search returned nothing"
    assert "Postgres" in hits[0][0].label, hits[0][0].label


def test_graph_contradiction_search_flags_temporal():
    graph = KnowledgeGraph()
    graph.add_node("The release ships on Monday", "fact", confidence=0.9)
    contradictions = graph.contradiction_search("The release is due on Friday", threshold=0.05)
    assert contradictions, "expected a temporal contradiction"
    assert any(c["reason"] == "temporal_conflict" for c in contradictions), contradictions


def test_graph_add_edge_creates_missing_endpoint_nodes():
    # Regression: add_edge() calls add_node() while holding _LOCK. With a
    # non-reentrant lock this deadlocks (hangs write_claim) whenever an
    # extracted relation's endpoints aren't already nodes. The reentrant lock
    # lets this return promptly and materialise both endpoint nodes.
    graph = KnowledgeGraph()
    before = graph.stats()["nodes"]
    graph.add_edge("brand new subject entity", "brand new object entity", "relates_to")
    after = graph.stats()["nodes"]
    assert after == before + 2, (before, after)


def test_graph_batch_coalesces_saves_into_one_write():
    # Regression: previously every add_node() rewrote the whole graph JSON,
    # so one memory ingest (many entities + relations) triggered N full-file
    # rewrites and could hang write_claim for >50s on a large graph. batch()
    # must defer the disk write so mutations don't touch the file inline; the
    # single flush() then persists every node at once.
    import os as _os
    graph = KnowledgeGraph()
    gf = graph._graph_file
    if _os.path.exists(gf):
        _os.remove(gf)
    with graph.batch():
        for i in range(40):
            graph.add_node(f"coalesce regression node {i}", "fact", confidence=0.9)
        # No inline rewrite happened while inside the batch.
        assert not _os.path.exists(gf), "batch() must not write the JSON inline"
    # Exiting the batch schedules a (debounced) save; flush() forces it now.
    graph.flush()
    assert _os.path.exists(gf), "flush() must persist the graph"
    import json as _json
    with open(gf) as f:
        labels = {n["label"] for n in _json.load(f)["nodes"]}
    assert all(f"coalesce regression node {i}" in labels for i in range(40))


def test_graph_flush_is_noop_when_clean():
    # flush() must be safe to call repeatedly and a no-op when nothing changed.
    graph = KnowledgeGraph()
    graph.add_node("a clean-flush regression fact", "fact")
    graph.flush()
    assert graph._dirty is False
    graph.flush()  # second call: nothing dirty, must not raise
    assert graph._dirty is False


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("ok   %s" % fn.__name__)
        except Exception as e:
            failed += 1
            print("FAIL %s: %s: %s" % (fn.__name__, type(e).__name__, e))
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
