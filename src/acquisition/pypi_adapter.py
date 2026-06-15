"""
PyPI registry adapter — searches PyPI's JSON API for candidate packages.

Secrets: none required. PyPI's search and project JSON endpoints are public.
Network: HTTPS only (enforced; any non-HTTPS URL raises). Bounded timeout.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from .interface import PackageCandidate, RegistryAdapter, RegistrySearchResult

logger = logging.getLogger(__name__)

_PYPI_SEARCH_URL = "https://pypi.org/search/"
_PYPI_PROJECT_URL = "https://pypi.org/pypi/{name}/json"
_TIMEOUT = 10


def _safe_get(url: str, **kwargs) -> Optional[object]:
    """HTTPS-enforced GET with timeout. Returns response or None on any error."""
    if not url.startswith("https://"):
        logger.warning("pypi_adapter: refusing non-HTTPS URL: %s", url)
        return None
    try:
        import urllib.request
        import urllib.error
        req = urllib.request.Request(url, headers={"User-Agent": "MyDude-acquisition/1.0"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp
    except Exception as exc:
        logger.debug("pypi_adapter: GET %s failed: %s", url, exc)
        return None


def _pypi_project_info(name: str) -> Optional[dict]:
    """Fetch the PyPI project JSON for a known package name."""
    import json
    url = _PYPI_PROJECT_URL.format(name=name)
    resp = _safe_get(url)
    if resp is None:
        return None
    try:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
        return data.get("info") or {}
    except Exception:
        return None


def _score_for_capability(desc: str, info: dict) -> float:
    """Heuristic relevance score 0..1 between capability desc and package info."""
    text = " ".join([
        info.get("name", ""),
        info.get("summary", ""),
        info.get("description", "")[:500],
        " ".join(info.get("keywords", "") or ""),
    ]).lower()
    words = re.findall(r"[a-z0-9]+", desc.lower())
    if not words:
        return 0.3
    hits = sum(1 for w in words if len(w) > 3 and w in text)
    return round(min(1.0, hits / max(len(words), 1)), 3)


class PyPIAdapter(RegistryAdapter):
    """Search PyPI for packages that plausibly satisfy a capability descriptor."""

    @property
    def registry_name(self) -> str:
        return "pypi"

    def search(
        self,
        capability_descriptor: str,
        *,
        max_results: int = 5,
    ) -> RegistrySearchResult:
        candidates = []
        error = None
        try:
            import json
            import urllib.parse
            import urllib.request

            query = " ".join(
                w for w in re.findall(r"[a-z0-9]+", capability_descriptor.lower())
                if len(w) > 2
            )[:100]
            if not query:
                return RegistrySearchResult(registry=self.registry_name, error="empty query")

            encoded = urllib.parse.urlencode({"q": query, "o": ""})
            search_url = f"{_PYPI_SEARCH_URL}?{encoded}"

            import html.parser

            class _PyPISearchParser(html.parser.HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.names = []
                    self._in_name = False

                def handle_starttag(self, tag, attrs):
                    attrs_d = dict(attrs)
                    if tag == "span" and "package-snippet__name" in attrs_d.get("class", ""):
                        self._in_name = True

                def handle_endtag(self, tag):
                    if tag == "span":
                        self._in_name = False

                def handle_data(self, data):
                    if self._in_name:
                        name = data.strip()
                        if name:
                            self.names.append(name)

            resp = _safe_get(search_url)
            names = []
            if resp is not None:
                try:
                    html_bytes = resp.read().decode("utf-8", errors="replace")
                    parser = _PyPISearchParser()
                    parser.feed(html_bytes)
                    names = parser.names[:max_results]
                except Exception as exc:
                    logger.debug("pypi_adapter: HTML parse error: %s", exc)

            for name in names[:max_results]:
                try:
                    info = _pypi_project_info(name)
                    if not info:
                        continue
                    score = _score_for_capability(capability_descriptor, info)
                    version = info.get("version", "")
                    candidates.append(PackageCandidate(
                        name=name,
                        version=version,
                        registry="pypi",
                        description=(info.get("summary") or "")[:200],
                        homepage=(info.get("home_page") or info.get("project_url") or "")[:200],
                        score=score,
                        install_spec=f"{name}=={version}" if version else name,
                    ))
                except Exception as exc:
                    logger.debug("pypi_adapter: info fetch for %s failed: %s", name, exc)

        except Exception as exc:
            error = str(exc)[:200]
            logger.warning("pypi_adapter.search failed: %s", exc)

        candidates.sort(key=lambda c: c.score, reverse=True)
        return RegistrySearchResult(
            registry=self.registry_name,
            candidates=candidates[:max_results],
            error=error,
        )
