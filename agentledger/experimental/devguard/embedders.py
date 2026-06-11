"""Embedding seam for DevGuard (experimental, dev-gated).

Provider-agnostic embedder selection (governance pillar #2). The default is a
local ONNX MiniLM model via :mod:`fastembed` — it runs on Linux CPU, pulls no
torch, and L2-normalizes its output (so cosine == inner product). If fastembed
is unavailable (offline, missing wheel) DevGuard falls back to the existing
:class:`~agentledger.experimental.memory_manager.LocalHashingEmbedder`, a real
deterministic token-overlap embedder — never a stub (pillar #1).

Both are exposed as the same ``Callable[[str], Sequence[float]]`` contract, so a
different provider (OpenAI, Gemini, or MLX on Apple-silicon VMs) can be injected
without touching call sites.
"""

from __future__ import annotations

import logging
from typing import Optional, Sequence

from ..gate import require_enabled
from ..memory_manager import Embedder, LocalHashingEmbedder

logger = logging.getLogger(__name__)

# Native dimensionality of all-MiniLM-L6-v2.
MINILM_DIM = 384
# Dimensionality of the offline hashing fallback.
HASHING_DIM = 256

DEFAULT_FASTEMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class FastEmbedEmbedder:
    """Local ONNX MiniLM embedder backed by :mod:`fastembed`.

    The model is lazy-loaded on first call, so constructing the embedder is cheap
    and import-safe. ``__call__`` returns a 384-dim L2-normalized vector.
    """

    def __init__(self, model_name: str = DEFAULT_FASTEMBED_MODEL) -> None:
        self.model_name = model_name
        self.dim = MINILM_DIM
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def __call__(self, text: str) -> list[float]:
        model = self._ensure_model()
        # fastembed yields one numpy array per input document.
        vec = next(iter(model.embed([text])))
        out = [float(x) for x in vec]
        if len(out) != self.dim:
            raise ValueError(
                f"fastembed returned dim {len(out)} != expected {self.dim} "
                f"for model {self.model_name!r}"
            )
        return out


def build_embedder(
    *,
    prefer_fastembed: bool = True,
    warm: bool = True,
    force: bool = False,
) -> tuple[Embedder, int]:
    """Return ``(embedder, dim)`` for DevGuard, honoring the dev gate.

    Parameters
    ----------
    prefer_fastembed:
        Try the local ONNX MiniLM model first. If ``False``, go straight to the
        hashing fallback (useful for fast, fully-offline tests).
    warm:
        When using fastembed, run one probe embedding so the model is downloaded
        and validated *here* (fail loud) rather than mid-index. Set ``False`` to
        defer the download to first real use.
    force:
        Bypass the production gate deliberately (explicit, auditable opt-in).

    Notes
    -----
    The returned ``dim`` is authoritative: callers must size the
    :class:`VectorStore` with it. fastembed (384) and the hashing fallback (256)
    differ, so an index built with one embedder must be rebuilt if the active
    embedder changes — :mod:`devguard.index` records the model used and enforces
    this.
    """
    require_enabled(force=force)

    if prefer_fastembed:
        try:
            import fastembed  # noqa: F401  (presence check)

            emb = FastEmbedEmbedder()
            if warm:
                probe = emb("def _devguard_probe():\n    return 1\n")
                return emb, len(probe)
            return emb, emb.dim
        except Exception as exc:  # env-dependent: missing wheel / offline
            logger.warning(
                "DevGuard: fastembed unavailable (%s); falling back to "
                "LocalHashingEmbedder (deterministic token-overlap semantics).",
                exc,
            )

    return LocalHashingEmbedder(dim=HASHING_DIM), HASHING_DIM


def embedder_id(embedder: Embedder) -> str:
    """Stable identifier for the active embedder, recorded alongside an index.

    Used to detect when an index was built with a different embedding model so it
    can be rebuilt rather than silently mixing incompatible vector spaces.
    """
    if isinstance(embedder, FastEmbedEmbedder):
        return f"fastembed:{embedder.model_name}:{embedder.dim}"
    if isinstance(embedder, LocalHashingEmbedder):
        return f"hashing:{embedder.dim}"
    return f"{type(embedder).__module__}.{type(embedder).__qualname__}"
