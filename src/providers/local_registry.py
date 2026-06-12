"""Local model registry reader.

Reads the per-install local model manifest (default
``~/.mydude/local/model_registry.yaml``) — the list of models that exist on
this machine for offline / sovereign inference (Ollama GGUF, Apple MLX).

The registry is OPTIONAL. When the file is absent (e.g. a cloud deployment with
no local sovereign stack installed) this degrades to an empty registry: the
local providers simply fall back to their ``default_model`` declared in env_1
(config/providers.toml). It is never a hard failure.

Used by:
  * the local LLM adapters (src/providers/adapters.py) to pick a sensible
    default local model per provider when none is pinned via env, and
  * the jurisdiction router (infra/mydude/routing/jurisdiction.py) to know which
    local models exist for the local_degraded fallback tier.
"""
import os
import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

DEFAULT_REGISTRY_PATH = "~/.mydude/local/model_registry.yaml"


def registry_path() -> Path:
    """Resolve the registry path (env override > default), expanding ``~``."""
    raw = os.environ.get("LOCAL_MODEL_REGISTRY_PATH") or DEFAULT_REGISTRY_PATH
    return Path(raw).expanduser()


def _extract_models(data) -> List[dict]:
    """Normalise the several shapes the registry file may take into a flat list.

    Accepts:
      * a bare list of model dicts
      * ``{"models": [...]}``
      * the sovereign_stack shape ``{"model_registry": {"format": [...]}}``
      * any mapping containing a list of dicts that look like model entries
    """
    if data is None:
        return []
    if isinstance(data, list):
        return [m for m in data if isinstance(m, dict) and m.get("model_id")]
    if isinstance(data, dict):
        if isinstance(data.get("models"), list):
            return _extract_models(data["models"])
        mr = data.get("model_registry")
        if isinstance(mr, dict) and isinstance(mr.get("format"), list):
            return _extract_models(mr["format"])
        # Best-effort: first value that is a list of model-like dicts.
        for v in data.values():
            if isinstance(v, list) and any(
                isinstance(m, dict) and m.get("model_id") for m in v
            ):
                return _extract_models(v)
    return []


def load_local_models() -> List[dict]:
    """Return the list of locally-registered models, or [] if unavailable.

    Never raises — a missing/malformed registry yields an empty list with a log
    line, so startup and routing degrade gracefully.
    """
    p = registry_path()
    if not p.exists():
        logger.info(
            "Local model registry not found at %s; no local models registered.", p
        )
        return []
    try:
        import yaml  # type: ignore
    except Exception:
        logger.warning(
            "pyyaml not available; cannot read local model registry at %s.", p
        )
        return []
    try:
        with open(p, "r") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        logger.warning("Failed to parse local model registry %s: %s", p, e)
        return []
    models = _extract_models(data)
    logger.info("Local model registry: %d model(s) loaded from %s", len(models), p)
    return models


def local_models_for_provider(provider: str) -> List[dict]:
    """All registered models whose ``provider`` matches (e.g. 'ollama', 'mlx')."""
    return [m for m in load_local_models() if m.get("provider") == provider]


def embedding_models() -> List[dict]:
    """All registered models tagged as embedding models (``kind: embedding``).

    The embedding capability (src/providers/embeddings.py) reads these to find a
    local-first embedding backend without any provider being hardwired. An entry
    looks like::

        - model_id: nomic-embed-text
          provider: ollama
          kind: embedding
          # optional: base_url, api_key_env, exec_locus

    Returns ``[]`` when none are registered, so embeddings degrade to TF-IDF.
    """
    return [
        m
        for m in load_local_models()
        if str(m.get("kind", "")).lower() == "embedding"
    ]


# --------------------------------------------------------------------------- #
# Writers — let operators edit the registry from the dashboard.
#
# These deliberately fail loudly (raise) rather than degrading silently the way
# the readers do: a corrupt or unwritable registry is an operator-facing error
# we want surfaced, not swallowed. The write itself is atomic (write to a temp
# file then ``os.replace``) so a failure can never leave a half-written,
# corrupt YAML behind.
# --------------------------------------------------------------------------- #

MAX_MODEL_ID_LEN = 256
MAX_PROVIDER_LEN = 64
MAX_META_KEYS = 32
MAX_META_KEY_LEN = 64
MAX_META_VALUE_LEN = 1024


def _require_yaml():
    try:
        import yaml  # type: ignore

        return yaml
    except Exception as e:  # pragma: no cover - pyyaml is a project dependency
        raise RuntimeError(
            "pyyaml is not available; cannot edit the local model registry."
        ) from e


def _raw_and_list(create: bool = False):
    """Return ``(data, models_list)`` where ``models_list`` is a *live* reference
    into ``data`` that can be mutated in place.

    Mirrors the shapes :func:`_extract_models` understands so edits preserve the
    file's existing structure. When the file/key is missing and ``create`` is
    True, a canonical ``{"models": []}`` container is materialised. When
    ``create`` is False and there is nothing to edit, returns ``(None, None)``.
    Raises ``ValueError`` on an unrecognisable (non-list, non-mapping) shape.
    """
    yaml = _require_yaml()
    p = registry_path()

    if not p.exists():
        if create:
            data: dict = {"models": []}
            return data, data["models"]
        return None, None

    with open(p, "r") as f:
        data = yaml.safe_load(f)

    if data is None:
        if create:
            data = {"models": []}
            return data, data["models"]
        return None, None

    if isinstance(data, list):
        # The document itself is the list of models.
        return data, data

    if isinstance(data, dict):
        if isinstance(data.get("models"), list):
            return data, data["models"]
        mr = data.get("model_registry")
        if isinstance(mr, dict) and isinstance(mr.get("format"), list):
            return data, mr["format"]
        for v in data.values():
            if isinstance(v, list) and any(
                isinstance(m, dict) and m.get("model_id") for m in v
            ):
                return data, v
        if create:
            data["models"] = []
            return data, data["models"]
        return data, []

    raise ValueError(
        "Unrecognised registry format at %s; refusing to edit it." % p
    )


def _write_registry(data) -> None:
    """Atomically serialise ``data`` back to the registry path.

    Creates the parent directory if missing. Serialises to a sibling temp file
    first and only ``os.replace``s it into place once the dump fully succeeds,
    so a serialisation error can never corrupt the existing registry.
    """
    yaml = _require_yaml()
    p = registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    try:
        with open(tmp, "w") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
        os.replace(tmp, p)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        raise


def add_model(model_id: str, provider: str) -> dict:
    """Add a ``{model_id, provider}`` entry to the registry and persist it.

    Creates the registry file (and ``~/.mydude/local/``) when missing. Raises
    ``ValueError`` for blank/oversized input or a duplicate entry. Returns the
    entry that was added.
    """
    model_id = (model_id or "").strip()
    provider = (provider or "").strip()
    if not model_id:
        raise ValueError("Model ID is required.")
    if not provider:
        raise ValueError("Provider is required.")
    if len(model_id) > MAX_MODEL_ID_LEN:
        raise ValueError("Model ID is too long (max %d characters)." % MAX_MODEL_ID_LEN)
    if len(provider) > MAX_PROVIDER_LEN:
        raise ValueError("Provider is too long (max %d characters)." % MAX_PROVIDER_LEN)

    data, models = _raw_and_list(create=True)
    for m in models:
        if (
            isinstance(m, dict)
            and m.get("model_id") == model_id
            and m.get("provider") == provider
        ):
            raise ValueError(
                "%s is already registered for %s." % (model_id, provider)
            )

    entry = {"model_id": model_id, "provider": provider}
    models.append(entry)
    _write_registry(data)
    logger.info("Added local model %s (%s) to registry %s", model_id, provider, registry_path())
    return entry


def _clean_metadata(details) -> dict:
    """Validate and normalise optional custom metadata for a registry entry.

    Accepts a mapping of extra key/value pairs (e.g. ``notes``,
    ``context_length``). Blank keys are dropped; the reserved ``model_id`` and
    ``provider`` keys are ignored here (they are set via dedicated fields).
    Raises ``ValueError`` on a non-mapping, oversized keys/values, non-scalar
    values, or too many entries. String values are stripped.
    """
    if details is None:
        return {}
    if not isinstance(details, dict):
        raise ValueError("Metadata must be a mapping of key/value pairs.")
    cleaned: dict = {}
    for k, v in details.items():
        key = str(k).strip()
        if not key:
            continue
        if key in ("model_id", "provider"):
            continue
        if len(key) > MAX_META_KEY_LEN:
            raise ValueError(
                "Metadata key %r is too long (max %d characters)." % (key, MAX_META_KEY_LEN)
            )
        if isinstance(v, (dict, list)):
            raise ValueError("Metadata value for %r must be a single value." % key)
        if isinstance(v, str):
            val = v.strip()
            if len(val) > MAX_META_VALUE_LEN:
                raise ValueError(
                    "Metadata value for %r is too long (max %d characters)."
                    % (key, MAX_META_VALUE_LEN)
                )
        else:
            val = v
        cleaned[key] = val
    if len(cleaned) > MAX_META_KEYS:
        raise ValueError("Too many metadata entries (max %d)." % MAX_META_KEYS)
    return cleaned


def update_model(
    model_id: str,
    provider: str,
    new_model_id: str = "",
    new_provider: str = "",
    details=None,
) -> dict:
    """Edit the entry matching ``model_id`` + ``provider`` in place and persist.

    Locates the existing entry by its current ``model_id``/``provider``, then
    replaces it with ``{new_model_id, new_provider, **details}``. Blank
    ``new_*`` values fall back to the originals, so callers can change only the
    metadata. Custom metadata fully replaces the entry's existing extra keys
    (the caller sends the desired final set), letting operators add or drop
    fields. Raises ``ValueError`` for blank/oversized input, a missing target
    entry, or a collision with a *different* existing entry. Returns the entry
    that was written.
    """
    model_id = (model_id or "").strip()
    provider = (provider or "").strip()
    new_model_id = (new_model_id or "").strip() or model_id
    new_provider = (new_provider or "").strip() or provider

    if not new_model_id:
        raise ValueError("Model ID is required.")
    if not new_provider:
        raise ValueError("Provider is required.")
    if len(new_model_id) > MAX_MODEL_ID_LEN:
        raise ValueError("Model ID is too long (max %d characters)." % MAX_MODEL_ID_LEN)
    if len(new_provider) > MAX_PROVIDER_LEN:
        raise ValueError("Provider is too long (max %d characters)." % MAX_PROVIDER_LEN)

    meta = _clean_metadata(details)

    data, models = _raw_and_list(create=False)
    if data is None or not models:
        raise ValueError("No matching model entry to edit.")

    target_idx = None
    for i, m in enumerate(models):
        if (
            isinstance(m, dict)
            and m.get("model_id") == model_id
            and m.get("provider") == provider
        ):
            target_idx = i
            break
    if target_idx is None:
        raise ValueError("No matching model entry to edit.")

    # Guard against colliding with a *different* existing entry.
    for i, m in enumerate(models):
        if i == target_idx:
            continue
        if (
            isinstance(m, dict)
            and m.get("model_id") == new_model_id
            and m.get("provider") == new_provider
        ):
            raise ValueError(
                "%s is already registered for %s." % (new_model_id, new_provider)
            )

    entry = {"model_id": new_model_id, "provider": new_provider}
    entry.update(meta)
    # Replace in place so the surrounding container shape is preserved.
    models[target_idx] = entry
    _write_registry(data)
    logger.info(
        "Updated local model %s (%s) -> %s (%s) in registry %s",
        model_id,
        provider,
        new_model_id,
        new_provider,
        registry_path(),
    )
    return entry


def remove_model(model_id: str, provider: str) -> None:
    """Remove the entry matching ``model_id`` + ``provider`` and persist.

    Raises ``ValueError`` when there is no registry to edit or no matching
    entry is found, so the UI can report it rather than silently no-op.
    """
    model_id = (model_id or "").strip()
    provider = (provider or "").strip()

    data, models = _raw_and_list(create=False)
    if data is None or not models:
        raise ValueError("No matching model entry to remove.")

    kept = [
        m
        for m in models
        if not (
            isinstance(m, dict)
            and m.get("model_id") == model_id
            and m.get("provider") == provider
        )
    ]
    if len(kept) == len(models):
        raise ValueError("No matching model entry to remove.")

    # Mutate the list in place so the surrounding container shape is preserved.
    models[:] = kept
    _write_registry(data)
    logger.info("Removed local model %s (%s) from registry %s", model_id, provider, registry_path())


def default_model_for_provider(provider: str, fallback: str = "") -> Optional[str]:
    """First registered model id for ``provider``, else ``fallback``.

    Lets a local adapter prefer an actually-installed model over its static
    config default, without requiring an env override.
    """
    models = local_models_for_provider(provider)
    if models:
        return models[0].get("model_id") or fallback
    return fallback
