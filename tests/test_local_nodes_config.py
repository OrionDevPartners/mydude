"""Tests for the Local AI Models *node endpoint* configuration surface.

Covers src/web/local_nodes.py — the helper that lets operators point the local
providers (Ollama / Apple MLX) at a localhost or Cloudflare Mesh base URL and
tune the TCP availability probe timeout from the dashboard, instead of editing
Replit Secrets by hand:

  * the allowed-key set is *derived* from config/providers.toml (every provider
    whose exec_locus is 'local' contributes its base_url env + a per-provider
    `<KEY>_PROBE_TIMEOUT`), plus the shared LOCAL_PROBE_TIMEOUT — never hardcoded;
  * node_settings() reflects what the live swarm resolves (base URL via get_env,
    is_default flag, and the same per-provider→shared→default timeout precedence
    used by src/swarm/jurisdiction.py);
  * update_node_settings() validates URLs + timeouts, rejects unknown keys, is
    all-or-nothing on a bad batch, and an empty value clears the override;
  * the on-demand TCP probe reports down (with host/port) for a closed port and
    refuses a URL with no usable host.

Settings persist in app_settings (non-secret) and are mirrored to os.environ by
settings_store, so we drive the store through a temporary SQLite DB and assert on
both the returned/applied dicts and the mirrored environment. No secret, network
server, or login round-trip is required: the probe simply finds nothing
listening.

Runnable two ways:
  * ``python tests/test_local_nodes_config.py``  (standalone, exits non-zero on failure)
  * ``pytest tests/test_local_nodes_config.py``   (test_* functions; no plugins needed)
"""
import asyncio
import os
import sys
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.web import local_nodes as ln


# -- env / settings isolation -------------------------------------------------

@contextmanager
def _clean_env():
    """Clear every key this surface manages, restoring prior state afterwards."""
    keys = ln._allowed_keys()
    saved = {k: os.environ.get(k) for k in keys}
    try:
        for k in keys:
            os.environ.pop(k, None)
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# -- allowed keys are derived from providers.toml, not hardcoded --------------

def test_allowed_keys_derived_from_local_providers():
    keys = ln._allowed_keys()
    # Shared timeout is always present.
    assert ln.SHARED_TIMEOUT_ENV in keys, keys
    # Every local provider contributes a base-url env + a per-provider timeout.
    specs = ln._local_specs()
    assert specs, "expected at least one exec_locus=local provider in config"
    for spec in specs:
        if spec.base_url_env:
            assert spec.base_url_env in keys, (spec.key, keys)
        assert ln._timeout_env_for(spec.key) in keys, (spec.key, keys)
    # The timeout env name matches the jurisdiction probe convention.
    assert ln._timeout_env_for("ollama") == "OLLAMA_PROBE_TIMEOUT"


# -- node_settings reflects live resolution -----------------------------------

def test_node_settings_defaults_when_unset():
    with _clean_env():
        data = ln.node_settings()
    assert data["default_probe_timeout"] == ln.DEFAULT_PROBE_TIMEOUT
    assert data["min_timeout"] == ln.MIN_TIMEOUT
    assert data["max_timeout"] == ln.MAX_TIMEOUT
    assert data["shared_probe_timeout"] == ""
    assert data["nodes"], "expected local provider nodes"
    for node in data["nodes"]:
        # Unset → falls back to the provider's default base URL and default timeout.
        assert node["base_url"] == node["default_base_url"]
        assert node["is_default"] is True
        assert node["probe_timeout"] == ""
        assert node["effective_timeout"] == ln.DEFAULT_PROBE_TIMEOUT


def test_effective_timeout_precedence():
    # per-provider override beats shared; shared beats default.
    with _clean_env():
        os.environ[ln.SHARED_TIMEOUT_ENV] = "3.0"
        os.environ["OLLAMA_PROBE_TIMEOUT"] = "2.0"
        data = ln.node_settings()
        by_key = {n["key"]: n for n in data["nodes"]}
        assert by_key["ollama"]["effective_timeout"] == 2.0
        # mlx has no per-provider override -> inherits the shared value.
        assert by_key["mlx"]["effective_timeout"] == 3.0
        assert data["shared_probe_timeout"] == "3.0"


# -- validation ---------------------------------------------------------------

def test_validate_url_rejects_non_http():
    for bad in ("ftp://x", "localhost:11434", "", "http://"):
        try:
            ln.validate_url(bad)
        except ValueError:
            continue
        raise AssertionError("expected ValueError for %r" % bad)
    # Good ones pass.
    ln.validate_url("http://localhost:11434/v1")
    ln.validate_url("https://100.96.0.1:11434/v1")


def test_validate_timeout_bounds():
    assert ln.validate_timeout("0.5") == 0.5
    for bad in ("abc", "0", "0.05", "31", "100"):
        try:
            ln.validate_timeout(bad)
        except ValueError:
            continue
        raise AssertionError("expected ValueError for %r" % bad)


# -- update_node_settings: persistence, clear, all-or-nothing -----------------

def _with_store():
    """Point settings_store at a throwaway SQLite DB. Returns a teardown fn."""
    import tempfile
    from src.web import settings_store as ss
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from src import database as db

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    engine = create_engine("sqlite:///%s" % tmp.name)
    db.Base.metadata.create_all(engine)
    Maker = sessionmaker(bind=engine)
    # settings_store binds SessionLocal into its own namespace at import time,
    # so patch it there (patching the database module wouldn't take effect).
    saved_local = ss.SessionLocal
    ss.SessionLocal = Maker

    def teardown():
        ss.SessionLocal = saved_local
        engine.dispose()
        os.unlink(tmp.name)

    return teardown


def test_update_persists_and_mirrors_env():
    teardown = _with_store()
    try:
        with _clean_env():
            applied = ln.update_node_settings({
                "OLLAMA_BASE_URL": "http://100.96.0.1:11434/v1",
                "OLLAMA_PROBE_TIMEOUT": "2.0",
                "LOCAL_PROBE_TIMEOUT": "3.0",
            })
            assert applied["OLLAMA_BASE_URL"] == "http://100.96.0.1:11434/v1"
            # Mirrored into the live environment so the swarm resolves it now.
            assert os.environ["OLLAMA_BASE_URL"] == "http://100.96.0.1:11434/v1"
            assert os.environ["OLLAMA_PROBE_TIMEOUT"] == "2.0"
            # node_settings reflects the override (no longer default).
            by_key = {n["key"]: n for n in ln.node_settings()["nodes"]}
            assert by_key["ollama"]["is_default"] is False
            assert by_key["ollama"]["effective_timeout"] == 2.0
    finally:
        teardown()


def test_empty_value_clears_override():
    teardown = _with_store()
    try:
        with _clean_env():
            ln.update_node_settings({"OLLAMA_BASE_URL": "http://100.96.0.1:11434/v1"})
            assert os.environ.get("OLLAMA_BASE_URL")
            ln.update_node_settings({"OLLAMA_BASE_URL": ""})
            # Cleared from the environment -> reverts to the code default.
            assert not os.environ.get("OLLAMA_BASE_URL")
            by_key = {n["key"]: n for n in ln.node_settings()["nodes"]}
            assert by_key["ollama"]["is_default"] is True
    finally:
        teardown()


def test_unknown_key_rejected():
    teardown = _with_store()
    try:
        with _clean_env():
            try:
                ln.update_node_settings({"FOO_BAR": "x"})
            except ValueError:
                pass
            else:
                raise AssertionError("expected ValueError for unknown key")
    finally:
        teardown()


def test_bad_batch_is_all_or_nothing():
    teardown = _with_store()
    try:
        with _clean_env():
            # One valid + one invalid entry -> nothing should be written.
            try:
                ln.update_node_settings({
                    "OLLAMA_BASE_URL": "http://100.96.0.1:11434/v1",
                    "LOCAL_PROBE_TIMEOUT": "999",  # out of range
                })
            except ValueError:
                pass
            else:
                raise AssertionError("expected ValueError for out-of-range timeout")
            # The valid sibling must NOT have been applied.
            assert not os.environ.get("OLLAMA_BASE_URL")
    finally:
        teardown()


# -- on-demand probe ----------------------------------------------------------

def test_probe_reports_down_for_closed_port():
    # Port 1 is reserved/closed; the probe should fail fast and report host/port.
    result = asyncio.run(ln.probe_endpoint("http://127.0.0.1:1/v1", 0.3))
    assert result["server_up"] is False
    assert result["host"] == "127.0.0.1"
    assert result["port"] == 1
    assert result.get("error")


def test_probe_rejects_url_without_host():
    result = asyncio.run(ln.probe_endpoint("http:///v1", 0.3))
    assert result["server_up"] is False
    assert "host" in result["error"].lower()


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
