"""Generic, provider-agnostic model resolution.

Given a provider key, a (possibly None) ``list_models`` callable and a list of
preference regexes (declared per-provider in env_1), pick the latest matching
model id and cache the result on disk. No vendor names are hardcoded here.
"""
import os
import re
import time
import json
from typing import Optional, List, Tuple, Callable, Awaitable

CACHE_PATH = "model_cache.json"
CACHE_TTL_SECONDS = int(os.getenv("MODEL_CACHE_TTL_SECONDS", "21600"))


def _load_cache() -> dict:
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(d: dict) -> None:
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _is_fresh(cache: dict, key: str) -> bool:
    ts = cache.get(key, {}).get("ts", 0)
    return (time.time() - ts) < CACHE_TTL_SECONDS


def _semver_key(name: str) -> Tuple[int, int, int]:
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", name)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
    m2 = re.search(r"opus-(\d+)-(\d+)(?:-(\d+))?", name)
    if m2:
        return (int(m2.group(1)), int(m2.group(2)), int(m2.group(3) or 0))
    return (0, 0, 0)


def pick_latest_matching(model_ids: List[str], prefix_regex: str) -> Optional[str]:
    pat = re.compile(prefix_regex)
    candidates = [m for m in model_ids if pat.search(m)]
    if not candidates:
        return None
    candidates.sort(key=_semver_key, reverse=True)
    return candidates[0]


async def resolve_model_cached(
    provider_key: str,
    list_models: Optional[Callable[[], Awaitable[List[str]]]],
    patterns: List[str],
    default_model: str,
) -> str:
    """Resolve the best model id for ``provider_key``.

    Tries the live model list (cached for CACHE_TTL_SECONDS), selecting the
    latest id matching any of ``patterns`` in order. Falls back to the cached
    value, then ``default_model``.
    """
    cache = _load_cache()

    if list_models and not _is_fresh(cache, provider_key):
        try:
            models = await list_models()
            picked = None
            for pat in patterns:
                picked = pick_latest_matching(models, pat)
                if picked:
                    break
            picked = picked or default_model
            cache[provider_key] = {"ts": time.time(), "model": picked}
            _save_cache(cache)
            return picked
        except Exception:
            return cache.get(provider_key, {}).get("model", default_model)

    return cache.get(provider_key, {}).get("model", default_model)
