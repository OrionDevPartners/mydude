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


# Tier / size words that disambiguate same-version variants. Flagship tiers
# outrank small/cheap variants so autorotate prefers the *frontier* model in a
# family (e.g. gpt-5.5 over gpt-5.5-mini) instead of an arbitrary tie-break.
# Operator pins (alias_env / anchored patterns / *_MODEL env) always win first;
# this only orders the candidates that a family pattern already matched.
_TIER_RANK = {
    "ultra": 4, "max": 3, "pro": 2, "plus": 1,
    "flash": -1, "air": -1, "lite": -2, "mini": -2,
    "nano": -3, "micro": -3,
}


def _tier_rank(name: str) -> int:
    low = name.lower()
    score = 0
    for word, val in _TIER_RANK.items():
        if re.search(r"(?<![a-z])%s(?![a-z])" % word, low):
            # Most decisive signal wins: largest magnitude, ties -> the higher
            # (more capable) tier.
            if abs(val) > abs(score) or (abs(val) == abs(score) and val > score):
                score = val
    return score


def _version_tuple(name: str) -> Tuple[int, int, int]:
    """Extract a (major, minor, patch) version from a model id, vendor-agnostic.

    Handles the family-versioning styles frontier vendors actually ship:
      * dotted semver        gpt-5.5, claude-3.7, qwen2.5  -> (5,5,0)/(3,7,0)
      * vN[.-N]              deepseek-v4, deepseek-v3-1     -> (4,0,0)/(3,1,0)
      * dashed family ver    claude-opus-4-8, claude-fable-5 -> (4,8,0)/(5,0,0)
      * attached            qwen3-max, grok4               -> (3,0,0)/(4,0,0)

    An 8-digit date snapshot (YYYYMMDD, e.g. claude-opus-4-20250514) is stripped
    first so a release date is never mistaken for a version segment.
    """
    cleaned = re.sub(r"[-_]?\d{8}(?![0-9])", "", name)
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", cleaned)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
    m = re.search(r"(?<![a-z0-9])v(\d+)(?:[.\-](\d+))?", cleaned, re.IGNORECASE)
    if m:
        return (int(m.group(1)), int(m.group(2) or 0), 0)
    m = re.search(r"-(\d+)(?:-(\d+))?(?:-(\d+))?", cleaned)
    if m:
        return (int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0))
    m = re.search(r"[a-z](\d+)", cleaned, re.IGNORECASE)
    if m:
        return (int(m.group(1)), 0, 0)
    return (0, 0, 0)


def _semver_key(name: str) -> Tuple[int, int, int, int]:
    major, minor, patch = _version_tuple(name)
    return (major, minor, patch, _tier_rank(name))


def pick_latest_matching(model_ids: List[str], prefix_regex: str) -> Optional[str]:
    pat = re.compile(prefix_regex)
    candidates = [m for m in (model_ids or []) if pat.search(m)]
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
