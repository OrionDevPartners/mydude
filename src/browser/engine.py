"""Browser engine — cost-ordered backend selection with automatic failover.

The engine resolves enabled backends from env_1, tries them cheapest-first, and
fails over to the next available backend when one errors. It reports which
backend ultimately served the request. Call sites never name a vendor.
"""
import logging
from typing import List

from src.browser.base import BrowserResult
from src.browser.config import ordered_backend_specs
from src.browser.registry import build_backend

logger = logging.getLogger(__name__)


class BrowserEngine:
    def backends(self):
        """Built backend instances in cost order (cheapest first)."""
        return [build_backend(spec) for spec in ordered_backend_specs()]

    def status(self) -> List[dict]:
        """Per-backend availability for the Capabilities UI / diagnostics."""
        rows = []
        for b in self.backends():
            rows.append({
                "key": b.key,
                "label": b.label,
                "adapter": b.spec.adapter,
                "cost": b.cost,
                "secrets": list(b.spec.secrets),
                "available": b.available(),
                "notes": b.spec.notes,
            })
        return rows

    async def open_page(
        self,
        url: str,
        *,
        timeout_ms: int = 30000,
        screenshot: bool = True,
        max_chars: int = 4000,
        allow_host=None,
    ) -> BrowserResult:
        attempts: List[str] = []
        last_error = None
        candidates = [b for b in self.backends() if b.available()]

        if not candidates:
            return BrowserResult(
                ok=False,
                url=url,
                error=(
                    "No browser backend is available. Enable a backend in "
                    "config/providers.toml and add its credentials in the vault "
                    "(or install the local Chromium build)."
                ),
            )

        for backend in candidates:
            attempts.append(backend.key)
            try:
                result = await backend.open_page(
                    url,
                    timeout_ms=timeout_ms,
                    screenshot=screenshot,
                    max_chars=max_chars,
                    allow_host=allow_host,
                )
                if result.ok:
                    result.attempts = attempts
                    return result
                # A policy block is not a backend failure — do not fail over and
                # retry the same forbidden navigation on the next backend.
                if result.blocked:
                    result.attempts = attempts
                    return result
                last_error = result.error
                logger.warning("Browser backend '%s' failed: %s", backend.key, result.error)
            except Exception as e:  # failover to the next backend
                last_error = "%s: %s" % (type(e).__name__, e)
                logger.warning("Browser backend '%s' raised: %s", backend.key, last_error)

        return BrowserResult(
            ok=False,
            url=url,
            error="All browser backends failed. Last error: %s" % (last_error or "unknown"),
            attempts=attempts,
        )
