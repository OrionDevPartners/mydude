import os
import re
import time
import json
from dataclasses import dataclass
from typing import Optional, List, Tuple

CACHE_PATH = "model_cache.json"
CACHE_TTL_SECONDS = int(os.getenv("MODEL_CACHE_TTL_SECONDS", "21600"))


@dataclass(frozen=True)
class ResolvedModels:
    openai: str
    anthropic: str
    gemini: str
    grok: str


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


async def resolve_models(
    openai_list_models=None,
    anthropic_alias: Optional[str] = None,
    gemini_list_models=None,
    grok_list_models=None,
) -> ResolvedModels:
    cache = _load_cache()

    if openai_list_models and not _is_fresh(cache, "openai"):
        try:
            o_models = await openai_list_models()
            openai_model = (
                pick_latest_matching(o_models, r"^gpt-4\.1(?!.*codex)") or
                pick_latest_matching(o_models, r"^gpt-4o") or
                os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
            )
            cache["openai"] = {"ts": time.time(), "model": openai_model}
        except Exception:
            openai_model = cache.get("openai", {}).get("model", os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))
    else:
        openai_model = cache.get("openai", {}).get("model", os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))

    anthropic_model = anthropic_alias or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

    if gemini_list_models and not _is_fresh(cache, "gemini"):
        try:
            g_models = await gemini_list_models()
            gemini_model = pick_latest_matching(g_models, r"gemini.*2.*flash") or os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
            cache["gemini"] = {"ts": time.time(), "model": gemini_model}
        except Exception:
            gemini_model = cache.get("gemini", {}).get("model", os.getenv("GEMINI_MODEL", "gemini-2.0-flash"))
    else:
        gemini_model = cache.get("gemini", {}).get("model", os.getenv("GEMINI_MODEL", "gemini-2.0-flash"))

    if grok_list_models and not _is_fresh(cache, "grok"):
        try:
            x_models = await grok_list_models()
            grok_model = pick_latest_matching(x_models, r"grok") or os.getenv("GROK_MODEL", "grok-2-latest")
            cache["grok"] = {"ts": time.time(), "model": grok_model}
        except Exception:
            grok_model = cache.get("grok", {}).get("model", os.getenv("GROK_MODEL", "grok-2-latest"))
    else:
        grok_model = cache.get("grok", {}).get("model", os.getenv("GROK_MODEL", "grok-2-latest"))

    _save_cache(cache)

    return ResolvedModels(
        openai=openai_model,
        anthropic=anthropic_model,
        gemini=gemini_model,
        grok=grok_model,
    )
