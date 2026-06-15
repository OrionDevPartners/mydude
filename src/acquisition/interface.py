"""
Provider-agnostic interfaces for capability acquisition (Pillar #2).

Registry adapters are swappable — callers only use RegistryAdapter /
KnowledgeAdapter; concrete implementations (PyPI, npm, web) are injected
at runtime, never hardcoded at call sites.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class PackageCandidate:
    """A single package candidate fetched from a registry."""
    name: str
    version: str
    registry: str
    description: str = ""
    homepage: str = ""
    score: float = 0.0
    install_spec: str = ""


@dataclass
class KnowledgeSummary:
    """A compact, non-secret knowledge excerpt from a web/doc source."""
    source_url: str
    title: str
    excerpt: str
    source_type: str = "web"


@dataclass
class RegistrySearchResult:
    """Combined result from one registry adapter search."""
    registry: str
    candidates: List[PackageCandidate] = field(default_factory=list)
    error: Optional[str] = None


class RegistryAdapter(abc.ABC):
    """Abstract adapter for a package registry (PyPI, npm, …)."""

    @property
    @abc.abstractmethod
    def registry_name(self) -> str: ...

    @abc.abstractmethod
    def search(
        self,
        capability_descriptor: str,
        *,
        max_results: int = 5,
    ) -> RegistrySearchResult:
        """Search the registry for packages matching the descriptor."""


class KnowledgeAdapter(abc.ABC):
    """Abstract adapter for harvesting supporting knowledge from web/docs."""

    @property
    @abc.abstractmethod
    def source_name(self) -> str: ...

    @abc.abstractmethod
    def harvest(
        self,
        package: PackageCandidate,
        *,
        max_chars: int = 3000,
    ) -> Optional[KnowledgeSummary]:
        """Harvest a compact knowledge summary for a given package."""
