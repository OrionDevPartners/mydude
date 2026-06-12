"""Local model node endpoint configuration + on-demand connectivity probe.

Operators running a local LLM server (Ollama / Apple MLX) — either on localhost
or on a separate box enrolled as a Cloudflare Mesh peer — can point the local
providers at the right base URL and tune the TCP availability probe timeout from
the dashboard, instead of editing Replit Secrets by hand.

Settings persist in ``app_settings`` (non-secret) and are mirrored into the
process environment by ``settings_store``, so the swarm's normal env-based config
path (the adapters' ``_base_url()`` / probe timeout) resolves them with no
restart required. The same resolution is reused by the jurisdiction governance
view and the live TCP probe, so nothing can drift.
"""
import asyncio
import logging
import os
import socket
import time
from urllib.parse import urlparse

from src.providers.config import llm_provider_specs, load_config
from src.providers.secrets import get_env
from src.web.settings_store import delete_setting, set_setting

logger = logging.getLogger(__name__)

# Probe timeout bounds (seconds). localhost is fast (~0.5s is plenty); Mesh hops
# need more headroom, so allow up to 30s while rejecting nonsense values.
DEFAULT_PROBE_TIMEOUT = 0.5
MIN_TIMEOUT = 0.1
MAX_TIMEOUT = 30.0

SHARED_TIMEOUT_ENV = "LOCAL_PROBE_TIMEOUT"

_LABELS = {"ollama": "Ollama", "mlx": "Apple MLX"}


def _local_specs() -> list:
    """ProviderSpecs for the enabled providers whose exec_locus is 'local'."""
    out = []
    try:
        providers = load_config().get("providers", {}) or {}
    except Exception as e:
        logger.debug("provider config load failed: %s", e)
        return out
    for spec in llm_provider_specs():
        if (providers.get(spec.key, {}) or {}).get("exec_locus") == "local":
            out.append(spec)
    return out


def _timeout_env_for(provider_key: str) -> str:
    return "%s_PROBE_TIMEOUT" % provider_key.upper()


def _is_timeout_key(key: str) -> bool:
    return key == SHARED_TIMEOUT_ENV or key.endswith("_PROBE_TIMEOUT")


def _effective_timeout(provider_key: str) -> float:
    """Resolve the timeout actually used: per-provider, then shared, then default.

    Mirrors the resolution in src/swarm/jurisdiction.py so the dashboard shows
    exactly what the live probe will use.
    """
    for env_name in (_timeout_env_for(provider_key), SHARED_TIMEOUT_ENV):
        val = os.environ.get(env_name, "").strip()
        if val:
            try:
                return float(val)
            except ValueError:
                pass
    return DEFAULT_PROBE_TIMEOUT


def _allowed_keys() -> set:
    """Setting keys an operator may write through this surface."""
    keys = {SHARED_TIMEOUT_ENV}
    for spec in _local_specs():
        if spec.base_url_env:
            keys.add(spec.base_url_env)
        keys.add(_timeout_env_for(spec.key))
    return keys


def node_settings() -> dict:
    """Current local-node endpoint configuration for the dashboard."""
    nodes = []
    for spec in _local_specs():
        base_env = spec.base_url_env
        resolved = (get_env(base_env, spec.default_base_url) or "") if base_env else ""
        timeout_env = _timeout_env_for(spec.key)
        nodes.append({
            "key": spec.key,
            "label": _LABELS.get(spec.key, spec.key.title()),
            "base_url_env": base_env or "",
            "base_url": resolved,
            "default_base_url": spec.default_base_url or "",
            "is_default": bool(spec.default_base_url) and resolved == spec.default_base_url,
            "probe_timeout_env": timeout_env,
            "probe_timeout": os.environ.get(timeout_env, "").strip(),
            "effective_timeout": _effective_timeout(spec.key),
        })
    return {
        "nodes": nodes,
        "shared_probe_timeout_env": SHARED_TIMEOUT_ENV,
        "shared_probe_timeout": os.environ.get(SHARED_TIMEOUT_ENV, "").strip(),
        "default_probe_timeout": DEFAULT_PROBE_TIMEOUT,
        "min_timeout": MIN_TIMEOUT,
        "max_timeout": MAX_TIMEOUT,
    }


def validate_url(url: str) -> None:
    """Raise ValueError if ``url`` is not a usable http(s) endpoint."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Endpoint URL must start with http:// or https://")
    if not parsed.hostname:
        raise ValueError("Endpoint URL must include a host.")


def validate_timeout(val: str) -> float:
    try:
        t = float(val)
    except ValueError:
        raise ValueError("Probe timeout must be a number of seconds.")
    if t < MIN_TIMEOUT or t > MAX_TIMEOUT:
        raise ValueError(
            "Probe timeout must be between %g and %g seconds." % (MIN_TIMEOUT, MAX_TIMEOUT)
        )
    return t


def update_node_settings(updates: dict) -> dict:
    """Validate and persist a batch of {env_var: value} local-node settings.

    An empty value clears the setting (reverts to the code default). Raises
    ValueError on an unknown key or an invalid value; nothing is written until
    every entry validates.
    """
    if not isinstance(updates, dict):
        raise ValueError("Expected an object of setting name/value pairs.")

    allowed = _allowed_keys()
    cleaned: list = []  # (key, value_or_None) after validation
    for raw_key, raw_val in updates.items():
        key = str(raw_key).strip()
        if key not in allowed:
            raise ValueError("Unknown setting: %s" % key)
        val = ("" if raw_val is None else str(raw_val)).strip()
        if not val:
            cleaned.append((key, None))
            continue
        if _is_timeout_key(key):
            cleaned.append((key, str(validate_timeout(val))))
        else:
            validate_url(val)
            cleaned.append((key, val))

    applied: dict = {}
    for key, val in cleaned:
        if val is None:
            delete_setting(key)
            applied[key] = ""
        else:
            set_setting(key, val)
            applied[key] = val
    return applied


def _probe(base_url: str, timeout: float) -> dict:
    parsed = urlparse(base_url)
    host = parsed.hostname
    if not host:
        return {"server_up": False, "error": "Invalid URL — no host."}
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            latency_ms = round((time.monotonic() - start) * 1000, 1)
            return {"server_up": True, "latency_ms": latency_ms, "host": host, "port": port}
    except Exception as e:
        return {
            "server_up": False,
            "host": host,
            "port": port,
            "error": str(e) or e.__class__.__name__,
        }


async def probe_endpoint(base_url: str, timeout: float) -> dict:
    """Run the blocking TCP connect probe off the event loop."""
    return await asyncio.to_thread(_probe, base_url, timeout)
