"""
Mem0Store — cloud-side memory store (vendored, trimmed).

Adapted from Mem0's memory store interface (Apache-2.0).
https://github.com/mem0ai/mem0

When MEM0_API_KEY is set, it proxies to the real Mem0 cloud API.
When unset it runs in local-file mode so the system stays self-contained
and air-gapped.  The bridge (src/memory/bridge.py) treats both modes
identically through the same add/search/get_all/delete interface.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DATA_DIR = Path(os.getenv("MEM0_DATA_DIR", ".mem0_data"))
_STORE_FILE = _DATA_DIR / "memories.json"
_MEM0_API_KEY = os.getenv("MEM0_API_KEY", "")
_MEM0_API_URL = os.getenv("MEM0_API_URL", "https://api.mem0.ai/v1")


@dataclass
class MemoryRecord:
    memory_id: str
    content: str
    user_id: str = "system"
    agent_id: str = "mydude"
    category: str = "fact"
    confidence: float = 1.0
    source: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    access_count: int = 0
    decay: float = 1.0
    metadata: Dict = field(default_factory=dict)


def _cosine_simple(a: str, b: str) -> float:
    """Fast bag-of-words cosine for local search ranking."""
    stop = {"the", "a", "an", "is", "are", "in", "of", "to", "and", "or", "it", "its"}
    ta = {w for w in re.split(r"\W+", a.lower()) if len(w) > 2 and w not in stop}
    tb = {w for w in re.split(r"\W+", b.lower()) if len(w) > 2 and w not in stop}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / (len(ta | tb) ** 0.5 * (len(ta) * len(tb)) ** 0.25)


class Mem0Store:
    """
    Unified Mem0-compatible memory store.

    Local mode: JSON file persistence under MEM0_DATA_DIR.
    Cloud mode: proxies to the Mem0 cloud REST API (requires MEM0_API_KEY).
    """

    def __init__(self, user_id: str = "system", agent_id: str = "mydude") -> None:
        self._user_id = user_id
        self._agent_id = agent_id
        self._records: Dict[str, MemoryRecord] = {}
        self._use_cloud = bool(_MEM0_API_KEY)
        if not self._use_cloud:
            self._load_local()

    def _load_local(self) -> None:
        try:
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
            if _STORE_FILE.exists():
                with open(_STORE_FILE) as f:
                    raw = json.load(f)
                for item in raw:
                    r = MemoryRecord(**item)
                    self._records[r.memory_id] = r
        except Exception as e:
            logger.warning("Mem0Store local load failed: %s", e)

    def _save_local(self) -> None:
        try:
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp = str(_STORE_FILE) + ".tmp"
            with open(tmp, "w") as f:
                json.dump([asdict(r) for r in self._records.values()], f)
            os.replace(tmp, str(_STORE_FILE))
        except Exception as e:
            logger.warning("Mem0Store local save failed: %s", e)

    def add(self, content: str, category: str = "fact",
            confidence: float = 1.0, source: str = "",
            metadata: Optional[Dict] = None) -> MemoryRecord:
        if self._use_cloud:
            return self._cloud_add(content, category, confidence, source, metadata)
        record = MemoryRecord(
            memory_id=str(uuid.uuid4()),
            content=content,
            user_id=self._user_id,
            agent_id=self._agent_id,
            category=category,
            confidence=confidence,
            source=source,
            metadata=metadata or {},
        )
        self._records[record.memory_id] = record
        self._save_local()
        return record

    def _local_add(self, content: str, category: str, confidence: float,
                   source: str, metadata: Optional[Dict]) -> MemoryRecord:
        """Write directly to the local record store (used as cloud fallback)."""
        record = MemoryRecord(
            memory_id=str(uuid.uuid4()),
            content=content,
            user_id=self._user_id,
            agent_id=self._agent_id,
            category=category,
            confidence=confidence,
            source=source,
            metadata=metadata or {},
        )
        self._records[record.memory_id] = record
        self._save_local()
        return record

    def _cloud_add(self, content: str, category: str, confidence: float,
                   source: str, metadata: Optional[Dict]) -> MemoryRecord:
        try:
            import urllib.request
            payload = json.dumps({
                "messages": [{"role": "user", "content": content}],
                "user_id": self._user_id,
                "agent_id": self._agent_id,
                "metadata": {
                    "category": category,
                    "confidence": confidence,
                    "source": source,
                    **(metadata or {}),
                },
            }).encode()
            req = urllib.request.Request(
                f"{_MEM0_API_URL}/memories/",
                data=payload,
                headers={
                    "Authorization": f"Token {_MEM0_API_KEY}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            mid = data.get("id") or str(uuid.uuid4())
            r = MemoryRecord(
                memory_id=mid,
                content=content,
                user_id=self._user_id,
                agent_id=self._agent_id,
                category=category,
                confidence=confidence,
                source=source,
                metadata=metadata or {},
            )
            self._records[r.memory_id] = r
            return r
        except Exception as e:
            logger.warning("Mem0 cloud add failed, using local fallback: %s", e)
            return self._local_add(content, category, confidence, source, metadata)

    def search(self, query: str, top_k: int = 5,
               category: Optional[str] = None) -> List[MemoryRecord]:
        if self._use_cloud:
            return self._cloud_search(query, top_k, category)
        candidates = list(self._records.values())
        if category:
            candidates = [r for r in candidates if r.category == category]
        scored = [(r, _cosine_simple(query, r.content)) for r in candidates]
        scored.sort(key=lambda x: x[1] * x[0].confidence * x[0].decay, reverse=True)
        for r, _ in scored[:top_k]:
            r.access_count += 1
        self._save_local()
        return [r for r, s in scored[:top_k] if s > 0.0]

    def _cloud_search(self, query: str, top_k: int,
                      category: Optional[str]) -> List[MemoryRecord]:
        try:
            import urllib.request, urllib.parse
            params = urllib.parse.urlencode({
                "query": query,
                "user_id": self._user_id,
                "limit": top_k,
            })
            req = urllib.request.Request(
                f"{_MEM0_API_URL}/memories/search/?{params}",
                headers={"Authorization": f"Token {_MEM0_API_KEY}"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            records = []
            for item in data.get("results", data if isinstance(data, list) else []):
                if isinstance(item, dict):
                    records.append(self._hydrate_record(item))
            return records[:top_k]
        except Exception as e:
            logger.warning("Mem0 cloud search failed, using local fallback: %s", e)
            return self._local_search(query, top_k, category)

    def _hydrate_record(self, item: dict) -> "MemoryRecord":
        """Build a MemoryRecord from a Mem0 API response dict, restoring key
        fields (confidence, category, source, timestamps) from the metadata
        envelope written by _cloud_add() so LWW merge is deterministic."""
        meta = item.get("metadata") or {}
        now = time.time()
        return MemoryRecord(
            memory_id=item.get("id") or str(uuid.uuid4()),
            content=item.get("memory", ""),
            user_id=self._user_id,
            agent_id=self._agent_id,
            category=meta.get("category", "fact"),
            confidence=float(meta.get("confidence", 0.5)),
            source=meta.get("source", "mem0_cloud"),
            created_at=float(meta.get("created_at", now)),
            updated_at=float(meta.get("updated_at", now)),
            decay=float(meta.get("decay", 1.0)),
            metadata=meta,
        )

    def _local_search(self, query: str, top_k: int,
                      category: Optional[str]) -> List[MemoryRecord]:
        """Search the local record cache directly (used as cloud fallback)."""
        candidates = list(self._records.values())
        if category:
            candidates = [r for r in candidates if r.category == category]
        scored = [(r, _cosine_simple(query, r.content)) for r in candidates]
        scored.sort(key=lambda x: x[1] * x[0].confidence * x[0].decay, reverse=True)
        for r, _ in scored[:top_k]:
            r.access_count += 1
        self._save_local()
        return [r for r, s in scored[:top_k] if s > 0.0]

    def get_all(self) -> List[MemoryRecord]:
        if self._use_cloud:
            return self._cloud_get_all()
        return list(self._records.values())

    def _cloud_get_all(self) -> List[MemoryRecord]:
        try:
            import urllib.request, urllib.parse
            params = urllib.parse.urlencode({"user_id": self._user_id, "limit": 500})
            req = urllib.request.Request(
                f"{_MEM0_API_URL}/memories/?{params}",
                headers={"Authorization": f"Token {_MEM0_API_KEY}"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            records = []
            for item in (data.get("results", data) if isinstance(data, (dict, list)) else []):
                if isinstance(item, dict):
                    records.append(self._hydrate_record(item))
            self._records = {r.memory_id: r for r in records}
            return records
        except Exception as e:
            logger.warning("Mem0 cloud get_all failed: %s", e)
            return list(self._records.values())

    def delete(self, memory_id: str) -> bool:
        if self._use_cloud:
            return self._cloud_delete(memory_id)
        if memory_id in self._records:
            del self._records[memory_id]
            self._save_local()
            return True
        return False

    def _cloud_delete(self, memory_id: str) -> bool:
        """Delete from the remote Mem0 API and local cache."""
        # Always purge from local cache so merge sees consistent state
        self._records.pop(memory_id, None)
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{_MEM0_API_URL}/memories/{memory_id}/",
                headers={"Authorization": f"Token {_MEM0_API_KEY}"},
                method="DELETE",
            )
            urllib.request.urlopen(req, timeout=10)
            return True
        except Exception as e:
            logger.warning("Mem0 cloud delete failed (local cache cleared): %s", e)
            return False

    def apply_decay(self, decay_rate: float = 0.005) -> None:
        import math
        now = time.time()
        for r in self._records.values():
            age_days = (now - r.updated_at) / 86400.0
            r.decay = max(0.1, r.decay * math.exp(-decay_rate * age_days))
        self._save_local()

    def stats(self) -> Dict:
        return {
            "records": len(self._records),
            "mode": "cloud" if self._use_cloud else "local",
            "data_file": str(_STORE_FILE) if not self._use_cloud else "N/A",
        }
