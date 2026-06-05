"""Vendor-agnostic browser backend contract.

A ``BrowserBackend`` knows how to open a URL and return a structured result.
Concrete backends live in ``backends.py`` and are selected by env_1
(config/providers.toml) through ``registry.py``. Calling code resolves
everything through ``engine.BrowserEngine`` and never names a vendor.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class BrowserBackendSpec:
    """A browser backend definition loaded from env_1 (config/providers.toml)."""

    key: str
    adapter: str
    cost: float = 0.0
    secrets: List[str] = field(default_factory=list)
    settings: Dict[str, str] = field(default_factory=dict)
    label: str = ""
    notes: str = ""


@dataclass
class BrowserResult:
    """Structured outcome of a browser navigation."""

    ok: bool
    backend: Optional[str] = None
    url: Optional[str] = None
    final_url: Optional[str] = None
    title: Optional[str] = None
    text: Optional[str] = None
    screenshot_b64: Optional[str] = None
    error: Optional[str] = None
    attempts: List[str] = field(default_factory=list)
    # True when navigation was aborted because a hop targeted a host outside the
    # allow-list. Distinct from a transport error so callers (and the failover
    # loop) can treat it as a policy block, not a backend failure to retry.
    blocked: bool = False


class BrowserBackend(ABC):
    """Vendor-agnostic browser backend contract."""

    def __init__(self, spec: BrowserBackendSpec):
        self.spec = spec

    @property
    def key(self) -> str:
        return self.spec.key

    @property
    def cost(self) -> float:
        return self.spec.cost

    @property
    def label(self) -> str:
        return self.spec.label or self.spec.key

    def secrets_present(self) -> bool:
        from src.providers.secrets import has_secret
        return all(has_secret(s) for s in self.spec.secrets)

    def available(self) -> bool:
        """True if this backend can currently be used (deps + secrets present).

        Must NOT raise — it drives cost-ordered selection and status display.
        """
        try:
            return self.secrets_present() and self._available()
        except Exception:
            return False

    @abstractmethod
    def _available(self) -> bool:
        """Backend-specific availability (deps installed, browser present, ...)."""

    @abstractmethod
    async def open_page(
        self,
        url: str,
        *,
        timeout_ms: int = 30000,
        screenshot: bool = True,
        max_chars: int = 4000,
        allow_host=None,
    ) -> BrowserResult:
        """Navigate to ``url`` and return a :class:`BrowserResult`.

        ``allow_host`` is an optional ``Callable[[str], bool]`` predicate. When
        provided, every in-browser navigation hop (including redirects) to a
        host for which it returns False is aborted before the request is made.
        """

    async def login_page(
        self,
        login_url: str,
        account_url: str,
        username: str,
        password: str,
        *,
        otp: str = None,
        timeout_ms: int = 45000,
        max_chars: int = 4000,
        allow_host=None,
    ) -> BrowserResult:
        """Log into a site and navigate to its account/billing page.

        Default: not supported. Interactive backends (Playwright-driven) override
        this. Returning an honest ``ok=False`` result lets the engine fail over.
        """
        return BrowserResult(
            ok=False,
            backend=self.key,
            url=login_url,
            error="Backend '%s' does not support interactive login." % self.key,
            attempts=[self.key],
        )

    async def cancel_action(
        self,
        login_url: str,
        account_url: str,
        username: str,
        password: str,
        *,
        otp: str = None,
        confirm_texts=None,
        timeout_ms: int = 45000,
        max_chars: int = 4000,
        allow_host=None,
    ) -> BrowserResult:
        """Log in, reach the account page, and click the cancel/confirm controls.

        Default: not supported. Interactive backends override this.
        """
        return BrowserResult(
            ok=False,
            backend=self.key,
            url=account_url or login_url,
            error="Backend '%s' does not support the cancel flow." % self.key,
            attempts=[self.key],
        )
