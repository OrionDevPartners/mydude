"""Browser capability adapter — bridge to the existing browser backend stack.

Wraps the existing ``src.browser`` package (BrowserBackend, registry, config)
behind the unified CapabilityAdapter interface with ZERO behavior change.
The cost-ordered failover, policy gating, and per-backend availability probes
all remain in the original stack and are completely preserved.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.capabilities.base import CapabilityAdapter, CapabilitySpec


class BrowserCapabilityAdapter(CapabilityAdapter):
    """Wraps the existing BrowserBackend (src.browser) behind the unified
    CapabilityAdapter interface.

    Each instance corresponds to one backend entry from env_1 (e.g.
    local_playwright, browserbase, apify, …). Availability is delegated to
    BrowserBackend.available() so the existing dep / secret logic is preserved.
    """

    def __init__(self, spec: CapabilitySpec) -> None:
        super().__init__(spec)
        self._browser_backend: Optional[object] = None
        self._built = False

    def _get_browser_backend(self):
        """Lazily build the underlying BrowserBackend from the existing stack."""
        if self._built:
            return self._browser_backend
        self._built = True
        try:
            from src.browser.config import defined_backend_specs
            from src.browser.registry import build_backend as _build_backend

            specs = defined_backend_specs()
            bspec = specs.get(self.spec.key)
            if bspec is None:
                self._browser_backend = None
                return None
            self._browser_backend = _build_backend(bspec)
        except Exception:
            self._browser_backend = None
        return self._browser_backend

    def secrets_present(self) -> bool:
        backend = self._get_browser_backend()
        if backend is None:
            return not self.spec.secrets
        try:
            return backend.secrets_present()
        except Exception:
            return False

    def _probe(self) -> bool:
        backend = self._get_browser_backend()
        if backend is None:
            return False
        try:
            return backend.available()
        except Exception:
            return False

    def is_available(self) -> bool:
        """Delegate entirely to the existing BrowserBackend availability logic."""
        try:
            backend = self._get_browser_backend()
            if backend is None:
                return False
            return backend.available()
        except Exception:
            return False

    def health_probe(self) -> Dict[str, Any]:
        ok = self.is_available()
        backend = self._get_browser_backend()
        label = ""
        if backend is not None:
            try:
                label = getattr(backend, "label", "") or ""
            except Exception:
                pass
        return {
            "ok": ok,
            "detail": ("available" + (" (%s)" % label if label else ""))
                      if ok else "unavailable (deps/secret missing or backend probe failed)",
            "exec_locus": self.spec.exec_locus,
        }

    def get_engine(self):
        """Return a BrowserEngine whose execution is strictly bound to the
        resolver-selected backend.

        This is the authoritative execution path for all browser capability
        calls — it guarantees that the backend actually used at runtime is
        exactly the one the resolver chose (honoring availability gates,
        jurisdiction / exec_locus_pin, cloud_shift, and cost ordering) and not
        any independently re-resolved candidate.

        BrowserEngine.backends() re-resolves all configured backends from
        env_1 (ordered_backend_specs) by default. We override it on the engine
        instance to return only this adapter's pre-resolved backend, making the
        resolver's selection authoritative at execution time with no re-resolution.
        """
        backend = self._get_browser_backend()
        if backend is None:
            raise RuntimeError(
                "BrowserCapabilityAdapter: no backend resolved for key=%r. "
                "Check [browser].enabled and secrets in config/providers.toml."
                % self.spec.key
            )
        from src.browser.engine import BrowserEngine
        engine = BrowserEngine()
        # Bind execution to exactly the resolver-selected backend by replacing
        # the instance-level backends() method. This closes the policy-bypass
        # gap: BrowserEngine.backends() normally re-resolves from config, which
        # could select a different backend than what the resolver chose.
        _resolved_backend = backend
        engine.backends = lambda: [_resolved_backend]
        return engine

    @property
    def exec_locus(self) -> str:
        return self.spec.exec_locus
