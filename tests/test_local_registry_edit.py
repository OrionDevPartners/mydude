"""Tests for the local model registry *edit* path (``update_model``).

Operators can edit an existing local model entry in place from the dashboard
(POST /api/local-models/registry/update), which renames its model_id/provider
and/or rewrites its custom metadata. Like the add/remove writers, the edit path
deliberately fails loudly (raises ValueError) and writes atomically rather than
degrading silently, so a bad edit can never corrupt or lose registry data.

These tests guard that edit path:
  * metadata-only edit — blank ``new_model_id``/``new_provider`` fall back to the
    originals, so callers can change only the extra fields;
  * rename of model_id and/or provider, persisted and visible to the reader;
  * metadata fully *replaces* the entry's existing extra keys (add or drop a
    field) and trims/validates values;
  * reserved ``model_id``/``provider`` keys inside ``details`` are ignored (the
    dedicated fields win, never the metadata);
  * collision with a *different* existing entry, a missing target entry, an
    absent registry, and blank/oversized input are all rejected (ValueError);
  * editing an entry to its own id/provider is NOT treated as a collision;
  * every supported container shape is preserved on write — a bare list,
    ``{models: [...]}`` and the sovereign_stack ``{model_registry: {format: [...]}}`` —
    and unrelated sibling keys survive untouched.

``_clean_metadata`` is covered directly too: blank-key drop, reserved-key drop,
string trimming, non-scalar rejection, the key/value length caps and the
max-entry-count cap, plus the non-mapping guard.

Each test points LOCAL_MODEL_REGISTRY_PATH at a throwaway temp file so nothing
touches the real ~/.mydude registry. No network, secret, or server required.

Runnable two ways:
  * ``python tests/test_local_registry_edit.py``  (standalone, exits non-zero on failure)
  * ``pytest tests/test_local_registry_edit.py``   (test_* functions; no plugins needed)
"""
import os
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from src.providers.local_registry import (
    add_model,
    update_model,
    load_local_models,
    _clean_metadata,
    MAX_MODEL_ID_LEN,
    MAX_PROVIDER_LEN,
    MAX_META_KEYS,
    MAX_META_KEY_LEN,
    MAX_META_VALUE_LEN,
)


@contextmanager
def _env(**overrides):
    """Temporarily set/unset env vars, restoring the prior state afterwards."""
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


@contextmanager
def _registry_at(initial_contents=None):
    """Point LOCAL_MODEL_REGISTRY_PATH at a fresh temp registry.

    When ``initial_contents`` is None the file is left absent (to exercise the
    no-registry path). Yields the resolved path.
    """
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "model_registry.yaml")
        if initial_contents is not None:
            with open(path, "w") as f:
                f.write(initial_contents)
        with _env(LOCAL_MODEL_REGISTRY_PATH=path):
            yield path


def _load_raw(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _assert_raises(exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc_type:
        return
    except Exception as e:  # wrong exception type
        raise AssertionError(
            "expected %s, got %s: %s" % (exc_type.__name__, type(e).__name__, e)
        )
    raise AssertionError("expected %s, but no exception was raised" % exc_type.__name__)


# -- update_model: metadata-only edit -----------------------------------------

def test_update_metadata_only_keeps_id_and_provider():
    # Blank new_model_id/new_provider fall back to the originals.
    with _registry_at() as path:
        add_model("llama3.2:3b", "ollama")
        entry = update_model(
            "llama3.2:3b", "ollama", details={"context_length": 8192, "notes": "fast"}
        )
        assert entry == {
            "model_id": "llama3.2:3b",
            "provider": "ollama",
            "context_length": 8192,
            "notes": "fast",
        }, entry
        # Persisted and visible to the reader.
        data = _load_raw(path)
        assert data == {"models": [entry]}, data
        assert load_local_models() == [entry]


def test_update_metadata_replaces_existing_extra_keys():
    # The caller sends the desired final metadata set, so old keys are dropped.
    initial = (
        "models:\n"
        "  - model_id: llama3.2:3b\n"
        "    provider: ollama\n"
        "    notes: old\n"
        "    context_length: 1\n"
    )
    with _registry_at(initial_contents=initial) as path:
        entry = update_model("llama3.2:3b", "ollama", details={"notes": "new"})
        # context_length is gone; only the supplied metadata remains.
        assert entry == {
            "model_id": "llama3.2:3b",
            "provider": "ollama",
            "notes": "new",
        }, entry
        data = _load_raw(path)
        assert data["models"][0] == entry, data


def test_update_with_no_details_drops_existing_metadata():
    initial = (
        "models:\n"
        "  - model_id: llama3.2:3b\n"
        "    provider: ollama\n"
        "    notes: old\n"
    )
    with _registry_at(initial_contents=initial) as path:
        entry = update_model("llama3.2:3b", "ollama")
        assert entry == {"model_id": "llama3.2:3b", "provider": "ollama"}, entry
        assert _load_raw(path)["models"][0] == entry


# -- update_model: rename ------------------------------------------------------

def test_update_renames_model_id_and_provider():
    with _registry_at() as path:
        add_model("llama3.2:3b", "ollama")
        entry = update_model(
            "llama3.2:3b", "ollama", new_model_id="qwen3:4b", new_provider="mlx"
        )
        assert entry == {"model_id": "qwen3:4b", "provider": "mlx"}, entry
        data = _load_raw(path)
        assert data == {"models": [entry]}, data
        # Old identity no longer present; reader reflects the rename.
        assert load_local_models() == [entry]


def test_update_trims_whitespace_on_lookup_and_rename():
    with _registry_at() as path:
        add_model("llama3.2:3b", "ollama")
        entry = update_model(
            "  llama3.2:3b  ", "  ollama  ",
            new_model_id="  qwen3:4b  ", new_provider="  mlx  ",
        )
        assert entry == {"model_id": "qwen3:4b", "provider": "mlx"}, entry
        assert _load_raw(path)["models"] == [entry]


def test_update_same_identity_is_not_a_self_collision():
    # Re-saving an entry with its own id/provider (e.g. metadata edit) is allowed.
    with _registry_at() as path:
        add_model("llama3.2:3b", "ollama")
        entry = update_model(
            "llama3.2:3b", "ollama",
            new_model_id="llama3.2:3b", new_provider="ollama",
            details={"notes": "x"},
        )
        assert entry == {
            "model_id": "llama3.2:3b",
            "provider": "ollama",
            "notes": "x",
        }, entry
        assert _load_raw(path)["models"] == [entry]


# -- update_model: reserved keys in details -----------------------------------

def test_update_ignores_reserved_keys_in_details():
    # model_id/provider inside details must not override the dedicated fields,
    # nor leak in as metadata.
    with _registry_at() as path:
        add_model("llama3.2:3b", "ollama")
        entry = update_model(
            "llama3.2:3b", "ollama",
            details={"model_id": "hijacked", "provider": "evil", "notes": "ok"},
        )
        assert entry == {
            "model_id": "llama3.2:3b",
            "provider": "ollama",
            "notes": "ok",
        }, entry
        assert _load_raw(path)["models"][0] == entry


# -- update_model: validation / failure modes ---------------------------------

def test_update_rejects_collision_with_different_entry():
    with _registry_at() as path:
        add_model("a", "ollama")
        add_model("b", "ollama")
        _assert_raises(
            ValueError, update_model, "a", "ollama",
            new_model_id="b", new_provider="ollama",
        )
        # Both entries are intact; nothing was overwritten.
        ids = {m["model_id"] for m in _load_raw(path)["models"]}
        assert ids == {"a", "b"}, ids


def test_update_rejects_missing_target():
    with _registry_at():
        add_model("a", "ollama")
        _assert_raises(ValueError, update_model, "does-not-exist", "ollama")
        # Right id, wrong provider, is still a miss.
        _assert_raises(ValueError, update_model, "a", "mlx")


def test_update_rejects_when_no_registry():
    with _registry_at(initial_contents=None):
        _assert_raises(ValueError, update_model, "a", "ollama")


def test_update_rejects_empty_registry_document():
    # An empty YAML document parses to None -> nothing to edit.
    with _registry_at(initial_contents="\n"):
        _assert_raises(ValueError, update_model, "a", "ollama")


def test_update_rejects_oversized_new_model_id():
    with _registry_at():
        add_model("a", "ollama")
        _assert_raises(
            ValueError, update_model, "a", "ollama",
            new_model_id="x" * (MAX_MODEL_ID_LEN + 1),
        )


def test_update_rejects_oversized_new_provider():
    with _registry_at():
        add_model("a", "ollama")
        _assert_raises(
            ValueError, update_model, "a", "ollama",
            new_provider="y" * (MAX_PROVIDER_LEN + 1),
        )


def test_update_rejects_bad_metadata():
    with _registry_at() as path:
        add_model("a", "ollama")
        _assert_raises(
            ValueError, update_model, "a", "ollama", details={"caps": {"nested": 1}}
        )
        # The failed edit left the entry untouched (no metadata partially applied).
        assert _load_raw(path)["models"] == [{"model_id": "a", "provider": "ollama"}]


# -- update_model: container-shape preservation -------------------------------

def test_update_preserves_bare_list_shape():
    initial = (
        "- model_id: llama3.2:3b\n"
        "  provider: ollama\n"
        "- model_id: phi3:mini\n"
        "  provider: ollama\n"
    )
    with _registry_at(initial_contents=initial) as path:
        update_model(
            "llama3.2:3b", "ollama", new_model_id="qwen3:4b", new_provider="mlx"
        )
        data = _load_raw(path)
        assert isinstance(data, list), type(data)
        assert {"model_id": "qwen3:4b", "provider": "mlx"} in data
        assert {"model_id": "phi3:mini", "provider": "ollama"} in data
        assert {"model_id": "llama3.2:3b", "provider": "ollama"} not in data


def test_update_preserves_models_mapping_shape():
    initial = (
        "models:\n"
        "  - model_id: llama3.2:3b\n"
        "    provider: ollama\n"
    )
    with _registry_at(initial_contents=initial) as path:
        update_model("llama3.2:3b", "ollama", details={"notes": "edited"})
        data = _load_raw(path)
        assert isinstance(data, dict) and "models" in data, data
        assert data["models"] == [
            {"model_id": "llama3.2:3b", "provider": "ollama", "notes": "edited"}
        ], data


def test_update_preserves_sovereign_stack_shape():
    initial = (
        "model_registry:\n"
        "  format:\n"
        "    - model_id: llama3.2:3b\n"
        "      provider: ollama\n"
    )
    with _registry_at(initial_contents=initial) as path:
        update_model(
            "llama3.2:3b", "ollama", new_model_id="qwen3:4b", new_provider="mlx"
        )
        data = _load_raw(path)
        assert isinstance(data, dict) and "model_registry" in data, data
        assert data["model_registry"]["format"] == [
            {"model_id": "qwen3:4b", "provider": "mlx"}
        ], data


def test_update_preserves_custom_mapping_shape():
    # _raw_and_list also accepts the best-effort fallback: a mapping whose first
    # value is a list of model-like dicts under an arbitrary key. The edit must
    # write back through that same custom key, not relocate it to ``models``.
    initial = (
        "custom_registry:\n"
        "  - model_id: llama3.2:3b\n"
        "    provider: ollama\n"
    )
    with _registry_at(initial_contents=initial) as path:
        update_model(
            "llama3.2:3b", "ollama", new_model_id="qwen3:4b", new_provider="mlx"
        )
        data = _load_raw(path)
        assert isinstance(data, dict) and "custom_registry" in data, data
        assert "models" not in data, data
        assert data["custom_registry"] == [
            {"model_id": "qwen3:4b", "provider": "mlx"}
        ], data


def test_update_preserves_extra_sibling_keys():
    initial = (
        "version: 7\n"
        "models:\n"
        "  - model_id: llama3.2:3b\n"
        "    provider: ollama\n"
    )
    with _registry_at(initial_contents=initial) as path:
        update_model("llama3.2:3b", "ollama", details={"notes": "x"})
        data = _load_raw(path)
        assert data.get("version") == 7, data
        assert data["models"] == [
            {"model_id": "llama3.2:3b", "provider": "ollama", "notes": "x"}
        ], data


# -- _clean_metadata -----------------------------------------------------------

def test_clean_metadata_none_returns_empty():
    assert _clean_metadata(None) == {}


def test_clean_metadata_rejects_non_mapping():
    _assert_raises(ValueError, _clean_metadata, ["not", "a", "mapping"])
    _assert_raises(ValueError, _clean_metadata, "string")


def test_clean_metadata_drops_blank_keys():
    cleaned = _clean_metadata({"": "x", "   ": "y", "keep": "z"})
    assert cleaned == {"keep": "z"}, cleaned


def test_clean_metadata_ignores_reserved_keys():
    cleaned = _clean_metadata({"model_id": "a", "provider": "b", "notes": "c"})
    assert cleaned == {"notes": "c"}, cleaned


def test_clean_metadata_strips_string_values():
    cleaned = _clean_metadata({"notes": "  hello  "})
    assert cleaned == {"notes": "hello"}, cleaned


def test_clean_metadata_keeps_non_string_scalars():
    cleaned = _clean_metadata({"ctx": 8192, "ratio": 0.5, "local": True})
    assert cleaned == {"ctx": 8192, "ratio": 0.5, "local": True}, cleaned


def test_clean_metadata_rejects_oversized_key():
    _assert_raises(ValueError, _clean_metadata, {"k" * (MAX_META_KEY_LEN + 1): "v"})


def test_clean_metadata_rejects_oversized_value():
    _assert_raises(ValueError, _clean_metadata, {"notes": "x" * (MAX_META_VALUE_LEN + 1)})


def test_clean_metadata_rejects_too_many_entries():
    too_many = {"k%d" % i: i for i in range(MAX_META_KEYS + 1)}
    _assert_raises(ValueError, _clean_metadata, too_many)


def test_clean_metadata_rejects_non_scalar_values():
    _assert_raises(ValueError, _clean_metadata, {"a": {"nested": 1}})
    _assert_raises(ValueError, _clean_metadata, {"a": [1, 2, 3]})


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
