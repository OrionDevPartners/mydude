"""Tests for the optional vector-embedding semantic layer.

The memory substrate (recall) and the consistency checker (contradiction
detection) gained an OPTIONAL real-embedding backend on top of their lexical
TF-IDF fallback. These tests prove:

  * with an embedding backend active, genuinely *semantic* matches that share no
    words are recalled / scored high — beating TF-IDF, which scores them ~0;
  * the temporal contradiction "finish by Friday" vs "deadline is Monday" — a
    pair with no shared content words — is now surfaced by the KG because the
    embedding similarity clears the gate that TF-IDF could not;
  * with NO backend (the default in this container) every path degrades cleanly
    to the existing TF-IDF / lexical behavior and never raises;
  * the resolver honors the cloud_shift kill switch (cloud backends are dropped
    when cloud egress is off; local backends still resolve);
  * embedding models are discovered from the local model registry.

A deterministic in-process *fake* backend is injected so the tests are hermetic
(no network, no model download, no secret). The fake maps known words to a tiny
concept space so paraphrases share concept dimensions while unrelated text does
not — exactly the property real sentence embeddings provide.

Runnable two ways:
  * ``python tests/test_embeddings_semantic.py``  (standalone, non-zero on failure)
  * ``pytest tests/test_embeddings_semantic.py``
"""
import os
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.providers import embeddings as emb
from src.providers.embeddings import EmbeddingBackend, cosine


# --------------------------------------------------------------------------- #
# Deterministic fake embedding backend
# --------------------------------------------------------------------------- #
_CONCEPTS = {
    # auth / identity
    "authentication": 0, "authenticate": 0, "verify": 0, "identities": 0,
    "identity": 0, "login": 0, "credentials": 0, "users": 0, "user": 0,
    "access": 0, "required": 0, "must": 0,
    # time / deadlines
    "deadline": 1, "due": 1, "finish": 1, "complete": 1, "done": 1,
    "friday": 1, "monday": 1, "tuesday": 1, "week": 1, "today": 1,
    "tomorrow": 1, "ship": 1,
    # database / storage
    "database": 2, "postgres": 2, "sql": 2, "records": 2, "record": 2,
    "table": 2, "stores": 2, "store": 2, "data": 2, "rows": 2,
}
_DIM = 3


class _FakeBackend(EmbeddingBackend):
    name = "fake-embeddings"
    exec_locus = "local"

    def is_available(self) -> bool:
        return True

    def embed(self, texts):
        out = []
        for t in texts:
            vec = [0.0] * _DIM
            for w in t.lower().replace(".", " ").replace(",", " ").split():
                idx = _CONCEPTS.get(w)
                if idx is not None:
                    vec[idx] += 1.0
            out.append(vec)
        return out


@contextmanager
def _fake_backend():
    """Inject the fake backend for the duration of the block."""
    import time
    emb.reset_embedding_backend()
    emb._emb_cache.clear()
    emb._backend = _FakeBackend()
    emb._backend_ts = time.monotonic()
    try:
        yield
    finally:
        emb.reset_embedding_backend()
        emb._emb_cache.clear()


@contextmanager
def _no_backend():
    """Ensure no backend resolves (no env config) for the duration."""
    saved = {k: os.environ.get(k) for k in (
        "EMBEDDING_MODEL", "EMBEDDING_PROVIDER", "EMBEDDING_BASE_URL",
        "EMBEDDING_API_KEY_ENV", "EMBEDDING_EXEC_LOCUS",
    )}
    for k in saved:
        os.environ.pop(k, None)
    emb.reset_embedding_backend()
    emb._emb_cache.clear()
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
        emb.reset_embedding_backend()
        emb._emb_cache.clear()


@contextmanager
def _env(**overrides):
    saved = {k: os.environ.get(k) for k in overrides}
    try:
        for k, v in overrides.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# --------------------------------------------------------------------------- #
# cosine helper
# --------------------------------------------------------------------------- #
def test_cosine_basic():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine([], [1.0]) == 0.0
    assert cosine([1.0, 2.0], [2.0, 4.0]) > 0.99  # colinear
    print("ok test_cosine_basic")


# --------------------------------------------------------------------------- #
# embedding helpers honor the fake backend
# --------------------------------------------------------------------------- #
def test_embed_helpers_with_backend():
    with _fake_backend():
        assert emb.embeddings_available() is True
        # paraphrase pair shares the "auth" concept -> high similarity
        s = emb.similarity("we need to verify identities", "authentication is required")
        assert s is not None and s > 0.9
        # unrelated pair -> ~0
        s2 = emb.similarity("we need to verify identities", "the database stores records")
        assert s2 is not None and s2 < 0.1
        scores = emb.rank_scores(
            "authentication required",
            ["we must verify user identities", "the database stores records"],
        )
        assert scores is not None
        assert scores[0] > scores[1]
    print("ok test_embed_helpers_with_backend")


def test_embed_helpers_without_backend():
    with _no_backend():
        assert emb.embeddings_available() is False
        assert emb.embed_text("anything") is None
        assert emb.similarity("a", "b") is None
        assert emb.rank_scores("a", ["b", "c"]) is None
        assert emb.rank_scores("a", []) == []
    print("ok test_embed_helpers_without_backend")


# --------------------------------------------------------------------------- #
# ConsistencyChecker uses embeddings for similarity, keeps TF-IDF fallback
# --------------------------------------------------------------------------- #
def test_consistency_similarity_semantic_vs_tfidf():
    from src.swarm.provenance import ConsistencyChecker

    # Paraphrase that shares NO content words with the verified fact.
    checker = ConsistencyChecker()
    checker.add_verified("authentication is required", "f1")

    with _no_backend():
        r_tfidf = checker.check_consistency("we must verify identities")
    with _fake_backend():
        r_emb = checker.check_consistency("we must verify identities")

    # TF-IDF sees ~no overlap; embeddings recognise the shared meaning.
    assert r_tfidf.similarity_score < 0.2
    assert r_emb.similarity_score > 0.8
    print("ok test_consistency_similarity_semantic_vs_tfidf")


def test_get_verified_context_ranks_paraphrase_first():
    from src.swarm.provenance import ConsistencyChecker

    checker = ConsistencyChecker()
    checker.add_verified("we must verify user identities", "auth")
    checker.add_verified("the database stores records", "db")

    with _fake_backend():
        ctx = checker.get_verified_context("authentication required", limit=2)
    # Embedding ranking puts the auth fact first despite zero shared words.
    assert ctx and ctx[0]["claim_id"] == "auth"
    assert ctx[0]["relevance"] > ctx[1]["relevance"]
    print("ok test_get_verified_context_ranks_paraphrase_first")


# --------------------------------------------------------------------------- #
# KG semantic_search + contradiction_search benefit from embeddings
# --------------------------------------------------------------------------- #
@contextmanager
def _temp_graph():
    """A KnowledgeGraph backed by a throwaway data dir."""
    from src.vendors.cognee import graph as g
    saved_dir, saved_file = g._DATA_DIR, g._GRAPH_FILE
    tmp = tempfile.mkdtemp(prefix="kg_test_")
    from pathlib import Path
    g._DATA_DIR = Path(tmp)
    g._GRAPH_FILE = g._DATA_DIR / "graph.json"
    try:
        yield g.KnowledgeGraph()
    finally:
        g._DATA_DIR, g._GRAPH_FILE = saved_dir, saved_file


def test_kg_semantic_search_finds_paraphrase():
    with _temp_graph() as kg:
        kg.add_node("we must verify user identities", entity_type="fact")
        kg.add_node("the database stores records", entity_type="fact")
        with _fake_backend():
            hits = kg.semantic_search("authentication required", top_k=2, min_score=0.1)
        assert hits, "embedding search returned nothing"
        assert "identities" in hits[0][0].label
    print("ok test_kg_semantic_search_finds_paraphrase")


def test_kg_temporal_contradiction_needs_embeddings():
    # "finish by Friday" vs "deadline is Monday" share no content words, so the
    # TF-IDF similarity gate (min_score) never surfaces them. Embeddings clear
    # the gate, and the temporal-conflict check then flags the contradiction.
    with _temp_graph() as kg:
        kg.add_node("deadline is Monday", entity_type="fact")
        with _no_backend():
            none_tfidf = kg.contradiction_search("we must finish by Friday", threshold=0.25)
        with _fake_backend():
            found = kg.contradiction_search("we must finish by Friday", threshold=0.25)
    assert none_tfidf == []  # TF-IDF could not surface it
    assert found and found[0]["reason"] == "temporal_conflict"
    print("ok test_kg_temporal_contradiction_needs_embeddings")


# --------------------------------------------------------------------------- #
# LocalMemoryAdapter cache ranking uses embeddings
# --------------------------------------------------------------------------- #
def test_local_adapter_rank_cached_semantic():
    from src.memory.local_store import LocalMemoryAdapter
    from src.memory.adapter import MemoryEntry

    adapter = LocalMemoryAdapter()
    entries = [
        MemoryEntry(memory_id="a", content="we must verify user identities", category="fact"),
        MemoryEntry(memory_id="b", content="the database stores records", category="fact"),
    ]
    with _fake_backend():
        scored = adapter._rank_cached("authentication required", entries)
    scored.sort(key=lambda x: x[1], reverse=True)
    assert scored and scored[0][0].memory_id == "a"

    with _no_backend():
        # No shared words with the lexical query -> word-overlap finds nothing.
        scored2 = adapter._rank_cached("authentication required", entries)
    assert scored2 == []
    print("ok test_local_adapter_rank_cached_semantic")


# --------------------------------------------------------------------------- #
# Resolver: cloud_shift gating + registry discovery
# --------------------------------------------------------------------------- #
def test_cloud_shift_gates_cloud_backend(monkeypatch=None):
    import src.swarm.jurisdiction as juris

    saved = juris.get_cloud_shift

    class _AlwaysAvail(EmbeddingBackend):
        def __init__(self, locus):
            self.exec_locus = locus
            self.name = f"fake-{locus}"

        def is_available(self):
            return True

        def embed(self, texts):
            return [[1.0] for _ in texts]

    def fake_spec_to_backend(spec):
        return _AlwaysAvail(spec.exec_locus)

    orig_spec = emb._spec_to_backend
    emb._spec_to_backend = fake_spec_to_backend
    try:
        # Cloud egress OFF -> a cloud embedding backend must be skipped.
        juris.get_cloud_shift = lambda: False
        with _env(EMBEDDING_MODEL="text-embedding-3-small",
                  EMBEDDING_PROVIDER="openai", EMBEDDING_EXEC_LOCUS=None):
            emb.reset_embedding_backend()
            assert emb.get_embedding_backend(force_refresh=True) is None

        # Cloud egress ON -> resolves the cloud backend.
        juris.get_cloud_shift = lambda: True
        with _env(EMBEDDING_MODEL="text-embedding-3-small",
                  EMBEDDING_PROVIDER="openai", EMBEDDING_EXEC_LOCUS=None):
            b = emb.get_embedding_backend(force_refresh=True)
            assert b is not None and b.exec_locus == "cloud"

        # Local backend resolves even with cloud egress OFF (sovereign).
        juris.get_cloud_shift = lambda: False
        with _env(EMBEDDING_MODEL="all-MiniLM-L6-v2",
                  EMBEDDING_PROVIDER="sentence-transformers", EMBEDDING_EXEC_LOCUS=None):
            b = emb.get_embedding_backend(force_refresh=True)
            assert b is not None and b.exec_locus == "local"
    finally:
        emb._spec_to_backend = orig_spec
        juris.get_cloud_shift = saved
        emb.reset_embedding_backend()
    print("ok test_cloud_shift_gates_cloud_backend")


def test_registry_embedding_models_discovery():
    import yaml
    from src.providers import local_registry

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "model_registry.yaml")
        with open(path, "w") as f:
            yaml.safe_dump({"models": [
                {"model_id": "llama3.2:3b", "provider": "ollama"},
                {"model_id": "nomic-embed-text", "provider": "ollama", "kind": "embedding"},
            ]}, f)
        with _env(LOCAL_MODEL_REGISTRY_PATH=path):
            ems = local_registry.embedding_models()
        assert len(ems) == 1
        assert ems[0]["model_id"] == "nomic-embed-text"
    print("ok test_registry_embedding_models_discovery")


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:  # pragma: no cover
            failed += 1
            print(f"ERROR {fn.__name__}: {e!r}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
