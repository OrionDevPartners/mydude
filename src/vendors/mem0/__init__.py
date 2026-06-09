"""
Mem0 — vendored cloud memory layer (trimmed).

Attribution: https://github.com/mem0ai/mem0 (Apache-2.0)
This copy retains only the memory store interface, add/search/delete
operations, and the client used by the MyDude.io sync bridge.
"""

from .store import Mem0Store, MemoryRecord

__all__ = ["Mem0Store", "MemoryRecord"]
