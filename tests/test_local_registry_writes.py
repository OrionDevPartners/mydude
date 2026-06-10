"""Tests for the local model registry *write* path.

Operators can add/remove local model entries from the dashboard
(POST /api/local-models/registry/{add,remove}), and those writes land in the
on-disk registry YAML (~/.mydude/local/model_registry.yaml). The readers in
src/providers/local_registry.py degrade silently, but the writers deliberately
fail loudly and write atomically so an edit can never corrupt or lose data.

These tests guard that write path:
  * add_model creates the file + parent dir when the registry is missing;
  * blank / oversized input and duplicate entries are rejected (ValueError);
  * remove_model rejects a non-existent entry (and an absent registry);
  * every supported container shape is preserved on write — a bare list,
    ``{models: [...]}`` and the sovereign_stack ``{model_registry: {format: [...]}}``;
  * atomicity — a serialisation failure leaves the original file byte-for-byte
    intact and leaves no stray temp file behind.

Each test points LOCAL_MODEL_REGISTRY_PATH at a throwaway temp file so nothing
touches the real ~/.mydude registry. No network, secret, or server required.

Runnable two ways:
  * ``python tests/test_local_registry_writes.py``  (standalone, exits non-zero on failure)
  * ``pytest tests/test_local_registry_writes.py``   (test_* functions; no plugins needed)
"""
import os
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from src.providers import local_registry
from src.providers.local_registry import (
    add_model,
    remove_model,
    load_local_models,
    MAX_MODEL_ID_LEN,
    MAX_PROVIDER_LEN,
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
def _registry_at(initial_contents=None, nested=False):
    """Point LOCAL_MODEL_REGISTRY_PATH at a fresh temp registry.

    When ``initial_contents`` is None the file is left absent (to exercise the
    create-on-missing path). When ``nested`` is True the path lives under a
    not-yet-existing subdirectory so we can prove the parent dir is created.
    Yields the resolved path.
    """
    with tempfile.TemporaryDirectory() as d:
        if nested:
            path = os.path.join(d, "deep", "nested", "model_registry.yaml")
        else:
            path = os.path.join(d, "model_registry.yaml")
        if initial_contents is not None:
            with open(path, "w") as f:
                f.write(initial_contents)
        with _env(LOCAL_MODEL_REGISTRY_PATH=path):
            yield path


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


# -- create-on-missing ---------------------------------------------------------

def test_add_creates_file_and_parent_dir_when_missing():
    with _registry_at(initial_contents=None, nested=True) as path:
        assert not os.path.exists(path)
        assert not os.path.isdir(os.path.dirname(path))
        entry = add_model("llama3.2:3b", "ollama")
        assert entry == {"model_id": "llama3.2:3b", "provider": "ollama"}
        # File (and its missing parent dirs) materialised.
        assert os.path.isfile(path), path
        # Persisted in the canonical {"models": [...]} container.
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data == {"models": [{"model_id": "llama3.2:3b", "provider": "ollama"}]}
        # And the reader sees it.
        assert load_local_models() == [
            {"model_id": "llama3.2:3b", "provider": "ollama"}
        ]


# -- input validation ----------------------------------------------------------

def test_add_rejects_blank_model_id():
    with _registry_at():
        _assert_raises(ValueError, add_model, "   ", "ollama")


def test_add_rejects_blank_provider():
    with _registry_at():
        _assert_raises(ValueError, add_model, "llama3.2:3b", "")


def test_add_rejects_oversized_model_id():
    with _registry_at():
        _assert_raises(ValueError, add_model, "x" * (MAX_MODEL_ID_LEN + 1), "ollama")


def test_add_rejects_oversized_provider():
    with _registry_at():
        _assert_raises(ValueError, add_model, "llama3.2:3b", "y" * (MAX_PROVIDER_LEN + 1))


def test_add_rejects_duplicate():
    with _registry_at() as path:
        add_model("llama3.2:3b", "ollama")
        _assert_raises(ValueError, add_model, "  llama3.2:3b  ", "ollama")
        # The duplicate must not have been appended.
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data == {"models": [{"model_id": "llama3.2:3b", "provider": "ollama"}]}


def test_add_trims_whitespace():
    with _registry_at() as path:
        entry = add_model("  llama3.2:3b  ", "  ollama  ")
        assert entry == {"model_id": "llama3.2:3b", "provider": "ollama"}
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["models"][0] == {"model_id": "llama3.2:3b", "provider": "ollama"}


# -- remove validation ---------------------------------------------------------

def test_remove_rejects_when_no_registry():
    with _registry_at(initial_contents=None):
        _assert_raises(ValueError, remove_model, "llama3.2:3b", "ollama")


def test_remove_rejects_nonexistent_entry():
    with _registry_at():
        add_model("llama3.2:3b", "ollama")
        _assert_raises(ValueError, remove_model, "does-not-exist", "ollama")
        _assert_raises(ValueError, remove_model, "llama3.2:3b", "mlx")


def test_remove_succeeds_and_persists():
    with _registry_at() as path:
        add_model("llama3.2:3b", "ollama")
        add_model("phi3:mini", "ollama")
        remove_model("llama3.2:3b", "ollama")
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data == {"models": [{"model_id": "phi3:mini", "provider": "ollama"}]}


# -- container-shape preservation ---------------------------------------------

def test_preserves_bare_list_shape():
    initial = (
        "- model_id: llama3.2:3b\n"
        "  provider: ollama\n"
    )
    with _registry_at(initial_contents=initial) as path:
        add_model("phi3:mini", "ollama")
        with open(path) as f:
            data = yaml.safe_load(f)
        # Still a bare list, with both entries.
        assert isinstance(data, list), type(data)
        assert {"model_id": "llama3.2:3b", "provider": "ollama"} in data
        assert {"model_id": "phi3:mini", "provider": "ollama"} in data
        # Removal preserves the list shape too.
        remove_model("llama3.2:3b", "ollama")
        with open(path) as f:
            data = yaml.safe_load(f)
        assert isinstance(data, list), type(data)
        assert data == [{"model_id": "phi3:mini", "provider": "ollama"}]


def test_preserves_models_mapping_shape():
    initial = (
        "models:\n"
        "  - model_id: llama3.2:3b\n"
        "    provider: ollama\n"
    )
    with _registry_at(initial_contents=initial) as path:
        add_model("phi3:mini", "ollama")
        with open(path) as f:
            data = yaml.safe_load(f)
        assert isinstance(data, dict) and "models" in data, data
        ids = {m["model_id"] for m in data["models"]}
        assert ids == {"llama3.2:3b", "phi3:mini"}, ids


def test_preserves_sovereign_stack_shape():
    initial = (
        "model_registry:\n"
        "  format:\n"
        "    - model_id: llama3.2:3b\n"
        "      provider: ollama\n"
    )
    with _registry_at(initial_contents=initial) as path:
        add_model("phi3:mini", "ollama")
        with open(path) as f:
            data = yaml.safe_load(f)
        # Outer container preserved: {model_registry: {format: [...]}}.
        assert isinstance(data, dict) and "model_registry" in data, data
        fmt = data["model_registry"]["format"]
        ids = {m["model_id"] for m in fmt}
        assert ids == {"llama3.2:3b", "phi3:mini"}, ids
        # Removal preserves the nested shape.
        remove_model("phi3:mini", "ollama")
        with open(path) as f:
            data = yaml.safe_load(f)
        assert isinstance(data, dict) and "model_registry" in data, data
        assert data["model_registry"]["format"] == [
            {"model_id": "llama3.2:3b", "provider": "ollama"}
        ]


def test_preserves_extra_sibling_keys():
    # A mapping with the models list plus unrelated metadata: the metadata must
    # survive an edit untouched (no silent data loss).
    initial = (
        "version: 7\n"
        "models:\n"
        "  - model_id: llama3.2:3b\n"
        "    provider: ollama\n"
    )
    with _registry_at(initial_contents=initial) as path:
        add_model("phi3:mini", "ollama")
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data.get("version") == 7, data
        assert len(data["models"]) == 2, data


def test_unrecognised_shape_is_rejected():
    # A scalar document is neither a list nor a mapping -> refuse to edit.
    with _registry_at(initial_contents="just a string\n"):
        _assert_raises(ValueError, add_model, "llama3.2:3b", "ollama")


# -- atomicity -----------------------------------------------------------------

@contextmanager
def _failing_safe_dump():
    """Make yaml.safe_dump raise, simulating a serialisation failure mid-write."""
    original = yaml.safe_dump

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated serialisation failure")

    yaml.safe_dump = _boom
    try:
        yield
    finally:
        yaml.safe_dump = original


def test_serialisation_failure_leaves_original_intact():
    initial = (
        "models:\n"
        "  - model_id: llama3.2:3b\n"
        "    provider: ollama\n"
    )
    with _registry_at(initial_contents=initial) as path:
        with open(path) as f:
            before = f.read()
        with _failing_safe_dump():
            _assert_raises(RuntimeError, add_model, "phi3:mini", "ollama")
        # Original file is byte-for-byte unchanged...
        with open(path) as f:
            after = f.read()
        assert after == before, "registry was modified despite a failed write"
        # ...and no stray temp file was left behind.
        tmp = path + ".tmp"
        assert not os.path.exists(tmp), "temp file leaked after failed write: %s" % tmp


def test_serialisation_failure_on_create_leaves_no_file():
    # When the registry does not yet exist and the write fails, no half-written
    # file (and no temp) should be left behind.
    with _registry_at(initial_contents=None) as path:
        with _failing_safe_dump():
            _assert_raises(RuntimeError, add_model, "llama3.2:3b", "ollama")
        assert not os.path.exists(path), "a file was created despite a failed write"
        assert not os.path.exists(path + ".tmp"), "temp file leaked on failed create"


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
