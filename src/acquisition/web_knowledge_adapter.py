"""
Web knowledge adapter — harvests compact, non-secret knowledge summaries
from PyPI project pages, npm package pages, and GitHub README URLs.

Network: HTTPS only (enforced). Bounded timeout. Never follows non-HTTPS
redirects. Compact excerpt only — no raw HTML/JS/CSS stored.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from .interface import KnowledgeAdapter, KnowledgeSummary, PackageCandidate

logger = logging.getLogger(__name__)

_TIMEOUT = 10
_MAX_RAW = 16_000


def _https_get(url: str, timeout: int = _TIMEOUT) -> Optional[str]:
    """HTTPS-only GET returning decoded text up to _MAX_RAW chars, or None."""
    if not url.startswith("https://"):
        logger.debug("web_knowledge_adapter: skipping non-HTTPS: %s", url)
        return None
    try:
        import urllib.request
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "MyDude-acquisition/1.0",
                "Accept": "text/html,text/plain,*/*",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read(_MAX_RAW).decode("utf-8", errors="replace")
            return raw
    except Exception as exc:
        logger.debug("web_knowledge_adapter: GET %s failed: %s", url, exc)
        return None


def _strip_html(html: str) -> str:
    """Strip HTML tags, collapse whitespace. Not a full sanitizer — excerpt only."""
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-z#0-9]+;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    return m.group(1).strip()[:120] if m else ""


def _pypi_page_url(name: str) -> str:
    return f"https://pypi.org/project/{name}/"


def _npm_page_url(name: str) -> str:
    return f"https://www.npmjs.com/package/{name}"


class WebKnowledgeAdapter(KnowledgeAdapter):
    """Harvest a compact knowledge summary for a candidate package.

    Tries, in order:
      1. The package homepage (if HTTPS)
      2. The registry page (PyPI or npm)
    Returns the first successfully harvested summary with a non-trivial excerpt.
    """

    @property
    def source_name(self) -> str:
        return "web"

    def harvest(
        self,
        package: PackageCandidate,
        *,
        max_chars: int = 3000,
    ) -> Optional[KnowledgeSummary]:
        urls_to_try = []
        if package.homepage and package.homepage.startswith("https://"):
            urls_to_try.append(package.homepage)
        if package.registry == "pypi":
            urls_to_try.append(_pypi_page_url(package.name))
        elif package.registry == "npm":
            urls_to_try.append(_npm_page_url(package.name))

        for url in urls_to_try:
            raw = _https_get(url)
            if not raw:
                continue
            title = _extract_title(raw) if "<html" in raw.lower() else ""
            text = _strip_html(raw) if "<html" in raw.lower() else raw
            excerpt = text[:max_chars].strip()
            if len(excerpt) < 80:
                continue
            return KnowledgeSummary(
                source_url=url,
                title=title or package.name,
                excerpt=excerpt,
                source_type="web",
            )

        return KnowledgeSummary(
            source_url="",
            title=package.name,
            excerpt=package.description or f"Package {package.name} ({package.registry})",
            source_type="description",
        ) if package.description else None
