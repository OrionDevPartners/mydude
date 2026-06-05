"""Backend registry — maps the ``adapter`` name declared in env_1 to a concrete
browser backend implementation. Register a new backend here once; selection and
configuration happen entirely in config/providers.toml.
"""
from src.browser.base import BrowserBackend, BrowserBackendSpec
from src.browser.backends import (
    LocalPlaywrightBackend,
    BrowserbaseBackend,
    ApifyBackend,
    AgentCoreBackend,
    AzureBackend,
)


class UnknownBrowserAdapterError(RuntimeError):
    """Raised when env_1 references a browser adapter that is not registered."""


BACKEND_REGISTRY = {
    "local_playwright": LocalPlaywrightBackend,
    "browserbase": BrowserbaseBackend,
    "apify": ApifyBackend,
    "agentcore": AgentCoreBackend,
    "azure": AzureBackend,
}


def build_backend(spec: BrowserBackendSpec) -> BrowserBackend:
    cls = BACKEND_REGISTRY.get(spec.adapter)
    if cls is None:
        raise UnknownBrowserAdapterError(
            "Unknown browser adapter '%s' for backend '%s'. Registered: %s"
            % (spec.adapter, spec.key, ", ".join(sorted(BACKEND_REGISTRY)))
        )
    return cls(spec)
