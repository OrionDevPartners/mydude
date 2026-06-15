"""
npm registry adapter — searches the npm registry JSON API for candidate packages.

Secrets: none required. npm's search endpoint is public.
Network: HTTPS only (enforced). Bounded timeout.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from .interface import PackageCandidate, RegistryAdapter, RegistrySearchResult

logger = logging.getLogger(__name__)

_NPM_SEARCH_URL = "https://registry.npmjs.org/-/v1/search"
_NPM_PACKAGE_URL = "https://registry.npmjs.org/{name}/latest"
_TIMEOUT = 10


def _safe_get_json(url: str) -> Optional[dict]:
    """HTTPS-enforced GET returning parsed JSON or None."""
    if not url.startswith("https://"):
        logger.warning("npm_adapter: refusing non-HTTPS URL: %s", url)
        return None
    try:
        import json
        import urllib.request
        req = urllib.request.Request(
            url, headers={"User-Agent": "MyDude-acquisition/1.0"}
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        logger.debug("npm_adapter: GET %s failed: %s", url, exc)
        return None


def _score_for_capability(desc: str, pkg: dict) -> float:
    text = " ".join([
        pkg.get("name", ""),
        pkg.get("description", ""),
        " ".join(pkg.get("keywords", []) or []),
    ]).lower()
    words = re.findall(r"[a-z0-9]+", desc.lower())
    if not words:
        return 0.3
    hits = sum(1 for w in words if len(w) > 3 and w in text)
    return round(min(1.0, hits / max(len(words), 1)), 3)


class NpmAdapter(RegistryAdapter):
    """Search npm for packages that plausibly satisfy a capability descriptor."""

    @property
    def registry_name(self) -> str:
        return "npm"

    def search(
        self,
        capability_descriptor: str,
        *,
        max_results: int = 5,
    ) -> RegistrySearchResult:
        candidates = []
        error = None
        try:
            import urllib.parse

            query = " ".join(
                w for w in re.findall(r"[a-z0-9]+", capability_descriptor.lower())
                if len(w) > 2
            )[:100]
            if not query:
                return RegistrySearchResult(registry=self.registry_name, error="empty query")

            params = urllib.parse.urlencode({"text": query, "size": max_results})
            data = _safe_get_json(f"{_NPM_SEARCH_URL}?{params}")
            if data is None:
                return RegistrySearchResult(
                    registry=self.registry_name,
                    error="search request failed",
                )

            for obj in (data.get("objects") or [])[:max_results]:
                try:
                    pkg = obj.get("package") or {}
                    name = pkg.get("name", "")
                    if not name:
                        continue
                    version = pkg.get("version", "")
                    description = (pkg.get("description") or "")[:200]
                    homepage = (pkg.get("links", {}).get("homepage") or
                                pkg.get("links", {}).get("npm") or "")[:200]
                    score = _score_for_capability(capability_descriptor, pkg)
                    candidates.append(PackageCandidate(
                        name=name,
                        version=version,
                        registry="npm",
                        description=description,
                        homepage=homepage,
                        score=score,
                        install_spec=f"{name}@{version}" if version else name,
                    ))
                except Exception as exc:
                    logger.debug("npm_adapter: parse error for object: %s", exc)

        except Exception as exc:
            error = str(exc)[:200]
            logger.warning("npm_adapter.search failed: %s", exc)

        candidates.sort(key=lambda c: c.score, reverse=True)
        return RegistrySearchResult(
            registry=self.registry_name,
            candidates=candidates[:max_results],
            error=error,
        )
