import json
from typing import Any


def safe_json_dumps(obj: Any, limit: int = 3500) -> str:
    s = json.dumps(obj, ensure_ascii=False, indent=2)
    return s[:limit]


def clamp_list(xs, n: int):
    return xs[:n] if xs else []
