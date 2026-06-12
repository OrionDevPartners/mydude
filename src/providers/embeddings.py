"""Provider-agnostic text embedding capability.

Adds genuinely *semantic* similarity on top of the lexical TF-IDF fallback used
across the memory substrate (recall) and the consistency checker (contradiction
detection). Two claims worded differently but meaning the same thing — e.g.
"we need to verify identities" vs "authentication is required" — score near 0
under TF-IDF but high under a real embedding model.

Embeddings are OPTIONAL. When no backend is available (no local embedding server
listening, no embedding model registered, cloud egress disabled with no local
option) every caller degrades to TF-IDF — never a hard failure.

Governance pillars honored:
  * **Provider-agnostic** — callers ask for an embedding, never a vendor. The
    concrete backend is resolved from env_1-style config (env vars + the local
    model registry), exactly like the LLM swarm resolves providers.
  * **Separate provider from secrets** — API keys are read by *name* via
    secrets.py; never hardcoded or passed around raw.
  * **Self-contained / sovereign** — when ``cloud_shift`` is off only
    ``exec_locus=local`` backends are eligible, so embeddings still run fully
    offline (sentence-transformers in-process, or a local Ollama/MLX server).

Backends
--------
* ``SentenceTransformerBackend`` — in-process sentence-transformers (e.g.
  ``sentence-transformers/all-MiniLM-L6-v2``). Local, no network, no secret.
* ``OpenAICompatEmbeddingBackend`` — any OpenAI-compatible ``/embeddings``
  endpoint. Covers a *local* server (Ollama ``nomic-embed-text``, an MLX
  embedding server) gated on a TCP probe, and a *cloud* endpoint
  (``text-embedding-3-small``) gated on its named secret + cloud_shift.

Configuration (first match wins)
--------------------------------
1. Env vars (operator override, no file needed)::

     EMBEDDING_MODEL          e.g. nomic-embed-text  /  all-MiniLM-L6-v2
     EMBEDDING_PROVIDER       ollama | mlx | openai | sentence-transformers
     EMBEDDING_BASE_URL       override the OpenAI-compatible endpoint
     EMBEDDING_API_KEY_ENV    NAME of the secret holding the key (cloud only)
     EMBEDDING_EXEC_LOCUS     local | cloud   (inferred from provider if unset)

2. The local model registry (``~/.mydude/local/model_registry.yaml``) — any
   entry tagged ``kind: embedding`` (see local_registry.embedding_models()).
"""
from __future__ import annotations

import logging
import math
import os
import socket
import threading
import time
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlparse

from src.providers.secrets import get_secret, get_env

logger = logging.getLogger(__name__)

try:  # numpy is already a dependency; pure-python fallback if it ever isn't.
    import numpy as _np  # type: ignore
except Exception:  # pragma: no cover - numpy is present in this project
    _np = None

try:
    from openai import OpenAI as _OpenAI  # type: ignore
except Exception:  # pragma: no cover - optional dependency guard
    _OpenAI = None


# Default OpenAI-compatible endpoints for the known local inference servers.
# Mirrors config/providers.toml so embeddings reach the same sovereign stack.
_PROVIDER_BASE_URLS = {
    "ollama": "http://localhost:11434/v1",
    "mlx": "http://localhost:11435/v1",
}

# Providers whose models run locally (no cloud egress). Used for cloud_shift
# gating: when cloud egress is off only these are eligible.
_LOCAL_PROVIDERS = {"ollama", "mlx", "sentence-transformers", "st", "local"}

_PLACEHOLDER_KEY = "local"


# --------------------------------------------------------------------------- #
# Vector math (numpy when available, pure-python otherwise)
# --------------------------------------------------------------------------- #
def cosine(a: Optional[List[float]], b: Optional[List[float]]) -> float:
    """Cosine similarity of two equal-length vectors. 0.0 on any degenerate input."""
    if not a or not b or len(a) != len(b):
        return 0.0
    try:
        if _np is not None:
            va = _np.asarray(a, dtype=float)
            vb = _np.asarray(b, dtype=float)
            na = float(_np.linalg.norm(va))
            nb = float(_np.linalg.norm(vb))
            if na == 0.0 or nb == 0.0:
                return 0.0
            return float(_np.dot(va, vb) / (na * nb))
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0.0 or nb == 0.0:
            return 0.0
        return dot / (na * nb)
    except Exception:
        return 0.0


# --------------------------------------------------------------------------- #
# Backend interface + implementations
# --------------------------------------------------------------------------- #
class EmbeddingBackend:
    """Vendor-agnostic embedding contract.

    Concrete backends embed text into dense vectors. ``is_available()`` gates a
    backend out (down local server, missing secret, missing package) so the
    resolver can fall through to the next candidate or to TF-IDF.
    """

    name: str = "embedding"
    exec_locus: str = "local"

    def is_available(self) -> bool:  # pragma: no cover - overridden
        raise NotImplementedError

    def embed(self, texts: List[str]) -> List[List[float]]:  # pragma: no cover
        raise NotImplementedError


class SentenceTransformerBackend(EmbeddingBackend):
    """In-process sentence-transformers backend (fully local, no network)."""

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id or "sentence-transformers/all-MiniLM-L6-v2"
        self.name = f"sentence-transformers:{self.model_id}"
        self.exec_locus = "local"
        self._model = None
        self._tried = False

    def _load(self):
        if self._tried:
            return self._model
        self._tried = True
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            self._model = SentenceTransformer(self.model_id)
        except Exception as exc:
            logger.info(
                "sentence-transformers backend unavailable (%s); embeddings will "
                "fall back to TF-IDF.", exc,
            )
            self._model = None
        return self._model

    def is_available(self) -> bool:
        return self._load() is not None

    def embed(self, texts: List[str]) -> List[List[float]]:
        model = self._load()
        if model is None:
            raise RuntimeError("sentence-transformers model not loaded")
        vecs = model.encode(list(texts), normalize_embeddings=False)
        try:
            return [list(map(float, v)) for v in vecs]
        except Exception:
            return [list(map(float, vecs))]  # single-vector edge case


class OpenAICompatEmbeddingBackend(EmbeddingBackend):
    """Any OpenAI-compatible ``/embeddings`` endpoint (local or cloud).

    Local endpoints (Ollama / MLX) need no secret and are gated on a cheap TCP
    probe of the base_url so a dead server never blocks the swarm. Cloud
    endpoints are gated on their named secret being present.
    """

    #: how long a server-up probe is trusted before re-probing (seconds)
    PROBE_TTL = 5.0

    def __init__(
        self,
        model: str,
        base_url: Optional[str],
        exec_locus: str,
        api_key_env: str = "",
    ) -> None:
        self.model = model
        self.base_url = base_url
        self.exec_locus = exec_locus or "cloud"
        self.api_key_env = api_key_env
        self.name = f"openai-compat:{model}@{base_url or 'default'}"
        self._client = None
        self._client_built = False
        self._probe_up = False
        self._probe_ts: Optional[float] = None

    # -- credentials (by name, never raw) ---------------------------------- #
    def _api_key(self) -> Optional[str]:
        if self.exec_locus == "local":
            # Local servers ignore the key but the SDK requires a non-empty one.
            return get_secret(self.api_key_env) or _PLACEHOLDER_KEY if self.api_key_env else _PLACEHOLDER_KEY
        return get_secret(self.api_key_env) if self.api_key_env else None

    def _build_client(self):
        if _OpenAI is None:
            return None
        key = self._api_key()
        if not key:
            return None
        kwargs = {"api_key": key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        try:
            return _OpenAI(**kwargs)
        except Exception as exc:  # pragma: no cover - defensive
            logger.info("embedding client build failed (%s)", exc)
            return None

    def _client_or_none(self):
        if not self._client_built:
            self._client = self._build_client()
            self._client_built = True
        return self._client

    def _probe_timeout(self) -> float:
        raw = (os.environ.get("LOCAL_PROBE_TIMEOUT", "") or "").strip()
        if raw:
            try:
                return float(raw)
            except ValueError:
                pass
        return 0.5

    def _server_listening(self) -> bool:
        if not self.base_url:
            return False
        try:
            parsed = urlparse(self.base_url)
            host = parsed.hostname or "localhost"
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            with socket.create_connection((host, port), timeout=self._probe_timeout()):
                return True
        except Exception:
            return False

    def is_available(self) -> bool:
        if self._client_or_none() is None:
            return False
        if self.exec_locus != "local":
            return True  # cloud: secret present + client built
        # Local: cache the TCP probe so repeated checks don't each block.
        now = time.monotonic()
        if self._probe_ts is None or (now - self._probe_ts) > self.PROBE_TTL:
            self._probe_up = self._server_listening()
            self._probe_ts = now
        return self._probe_up

    def embed(self, texts: List[str]) -> List[List[float]]:
        client = self._client_or_none()
        if client is None:
            raise RuntimeError("embedding client unavailable")
        resp = client.embeddings.create(model=self.model, input=list(texts))
        return [list(map(float, d.embedding)) for d in resp.data]


# --------------------------------------------------------------------------- #
# Candidate resolution (env first, then the local model registry)
# --------------------------------------------------------------------------- #
@dataclass
class _EmbeddingSpec:
    model: str
    provider: str
    base_url: Optional[str]
    exec_locus: str
    api_key_env: str


def _infer_exec_locus(provider: str, base_url: Optional[str], explicit: str) -> str:
    if explicit:
        return explicit.strip().lower()
    if provider in _LOCAL_PROVIDERS:
        return "local"
    if base_url:
        host = (urlparse(base_url).hostname or "").lower()
        if host in ("localhost", "127.0.0.1", "::1"):
            return "local"
    return "cloud"


def _spec_to_backend(spec: _EmbeddingSpec) -> Optional[EmbeddingBackend]:
    provider = (spec.provider or "").lower()
    if provider in ("sentence-transformers", "st"):
        return SentenceTransformerBackend(spec.model)
    base_url = spec.base_url or _PROVIDER_BASE_URLS.get(provider)
    return OpenAICompatEmbeddingBackend(
        model=spec.model,
        base_url=base_url,
        exec_locus=spec.exec_locus,
        api_key_env=spec.api_key_env,
    )


def _env_spec() -> Optional[_EmbeddingSpec]:
    model = get_env("EMBEDDING_MODEL")
    if not model:
        return None
    provider = (get_env("EMBEDDING_PROVIDER", "") or "").lower()
    base_url = get_env("EMBEDDING_BASE_URL")
    api_key_env = get_env("EMBEDDING_API_KEY_ENV", "") or ""
    exec_locus = _infer_exec_locus(provider, base_url, get_env("EMBEDDING_EXEC_LOCUS", "") or "")
    return _EmbeddingSpec(model, provider, base_url, exec_locus, api_key_env)


def _registry_specs() -> List[_EmbeddingSpec]:
    specs: List[_EmbeddingSpec] = []
    try:
        from src.providers.local_registry import embedding_models

        for entry in embedding_models():
            model = entry.get("model_id")
            if not model:
                continue
            provider = (entry.get("provider") or "").lower()
            base_url = entry.get("base_url")
            api_key_env = entry.get("api_key_env", "") or ""
            exec_locus = _infer_exec_locus(
                provider, base_url, str(entry.get("exec_locus", "") or "")
            )
            specs.append(
                _EmbeddingSpec(model, provider, base_url, exec_locus, api_key_env)
            )
    except Exception as exc:
        logger.debug("embedding registry read failed (%s)", exc)
    return specs


def _candidate_specs() -> List[_EmbeddingSpec]:
    specs: List[_EmbeddingSpec] = []
    env = _env_spec()
    if env is not None:
        specs.append(env)
    specs.extend(_registry_specs())
    return specs


def _cloud_egress_allowed() -> bool:
    try:
        from src.swarm.jurisdiction import get_cloud_shift

        return bool(get_cloud_shift())
    except Exception:
        # No jurisdiction layer wired — default to permitting cloud (matches
        # get_cloud_shift's own default) but never crash resolution.
        return True


# --------------------------------------------------------------------------- #
# Resolved-backend singleton (cached with a short TTL)
# --------------------------------------------------------------------------- #
_RESOLVE_TTL = 30.0
_backend: Optional[EmbeddingBackend] = None
_backend_ts: float = 0.0
_resolve_lock = threading.Lock()


def _resolve() -> Optional[EmbeddingBackend]:
    cloud_ok = _cloud_egress_allowed()
    for spec in _candidate_specs():
        if not cloud_ok and spec.exec_locus != "local":
            logger.debug(
                "embedding candidate %s skipped: cloud_shift off, exec_locus=%s",
                spec.model, spec.exec_locus,
            )
            continue
        try:
            backend = _spec_to_backend(spec)
        except Exception as exc:
            logger.debug("embedding backend build failed for %s (%s)", spec.model, exc)
            continue
        if backend is not None and backend.is_available():
            logger.info("Embedding backend active: %s (exec_locus=%s)",
                        backend.name, backend.exec_locus)
            return backend
    return None


def get_embedding_backend(force_refresh: bool = False) -> Optional[EmbeddingBackend]:
    """Return the active embedding backend, or None to signal TF-IDF fallback.

    Resolution is cached for ``_RESOLVE_TTL`` so the hot paths (recall, every
    consistency check) don't re-probe servers on each call. Never raises.
    """
    global _backend, _backend_ts
    now = time.monotonic()
    if force_refresh or _backend is None or (now - _backend_ts) > _RESOLVE_TTL:
        with _resolve_lock:
            if force_refresh or _backend is None or (now - _backend_ts) > _RESOLVE_TTL:
                try:
                    _backend = _resolve()
                except Exception as exc:
                    logger.debug("embedding resolution failed (%s)", exc)
                    _backend = None
                _backend_ts = now
    return _backend


def embeddings_available() -> bool:
    return get_embedding_backend() is not None


def reset_embedding_backend() -> None:
    """Drop the cached backend (used by tests and after config changes)."""
    global _backend, _backend_ts
    with _resolve_lock:
        _backend = None
        _backend_ts = 0.0


# --------------------------------------------------------------------------- #
# Cached embedding helpers used by callers
# --------------------------------------------------------------------------- #
_EMB_CACHE_MAX = 4096
_emb_cache: "dict[tuple, List[float]]" = {}
_emb_cache_lock = threading.Lock()


def embed_texts(texts: List[str]) -> Optional[List[List[float]]]:
    """Embed a batch of texts, or None if no backend is available.

    Per-text results are memoised (keyed by backend name + text) so embedding
    the same corpus across many similarity checks is cheap. Returns None — never
    raises — so callers cleanly fall back to TF-IDF.
    """
    backend = get_embedding_backend()
    if backend is None:
        return None
    try:
        results: List[Optional[List[float]]] = [None] * len(texts)
        misses: List[str] = []
        miss_idx: List[int] = []
        with _emb_cache_lock:
            for i, t in enumerate(texts):
                hit = _emb_cache.get((backend.name, t))
                if hit is not None:
                    results[i] = hit
                else:
                    misses.append(t)
                    miss_idx.append(i)
        if misses:
            vecs = backend.embed(misses)
            if len(vecs) != len(misses):
                return None
            with _emb_cache_lock:
                if len(_emb_cache) > _EMB_CACHE_MAX:
                    _emb_cache.clear()
                for idx, t, v in zip(miss_idx, misses, vecs):
                    results[idx] = v
                    _emb_cache[(backend.name, t)] = v
        return [r if r is not None else [] for r in results]
    except Exception as exc:
        logger.warning("embed_texts failed (%s); falling back to TF-IDF", exc)
        return None


def embed_text(text: str) -> Optional[List[float]]:
    out = embed_texts([text])
    if not out:
        return None
    return out[0]


def similarity(text_a: str, text_b: str) -> Optional[float]:
    """Embedding cosine similarity of two texts, or None if unavailable."""
    out = embed_texts([text_a, text_b])
    if not out or len(out) != 2:
        return None
    return cosine(out[0], out[1])


def rank_scores(query: str, candidates: List[str]) -> Optional[List[float]]:
    """Cosine similarity of ``query`` against each candidate, aligned by index.

    Returns None when no backend is available so callers fall back to TF-IDF.
    """
    if not candidates:
        return []
    out = embed_texts([query] + list(candidates))
    if not out or len(out) != len(candidates) + 1:
        return None
    q = out[0]
    return [cosine(q, c) for c in out[1:]]
