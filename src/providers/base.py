"""Provider-agnostic interface for LLM providers.

Code calls ``LLMAdapter.generate`` / ``LLMAdapter.list_models`` and never knows
which vendor is behind it. Concrete adapters live in ``adapters.py`` and are
selected by env_1 (config/providers.toml) through the registry.
"""
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

from src.providers.secrets import get_secret, has_secret, get_env


@dataclass
class ProviderSpec:
    """A provider definition loaded from env_1 (config/providers.toml)."""
    key: str
    adapter: str
    secrets: List[str] = field(default_factory=list)
    model_env: str = ""
    default_model: str = ""
    concurrency_env: str = ""
    default_concurrency: int = 2
    role_hint: str = ""
    model_patterns: List[str] = field(default_factory=list)
    base_url_env: str = ""
    default_base_url: str = ""
    alias_env: str = ""


class LLMAdapter(ABC):
    """Vendor-agnostic LLM provider contract."""

    def __init__(self, spec: ProviderSpec):
        self.spec = spec
        self._client = None
        self._client_built = False
        self._model = spec.default_model
        self._resolved = False

    # -- identity / config (no vendor names in calling code) ------------------
    @property
    def key(self) -> str:
        return self.spec.key

    @property
    def required_secrets(self) -> List[str]:
        return list(self.spec.secrets)

    @property
    def role_hint(self) -> str:
        return self.spec.role_hint

    @property
    def model(self) -> str:
        return self._model

    # -- availability ---------------------------------------------------------
    def secrets_present(self) -> bool:
        return all(has_secret(s) for s in self.spec.secrets)

    def client(self):
        if not self._client_built:
            self._client = self._build_client() if self.secrets_present() else None
            self._client_built = True
        return self._client

    def is_available(self) -> bool:
        return self.secrets_present() and self.client() is not None

    # -- model resolution (generic, driven by env_1 patterns) -----------------
    async def resolve_model(self) -> str:
        if self._resolved:
            return self._model
        from src.swarm.model_resolver import resolve_model_cached

        env_model = get_env(self.spec.model_env) if self.spec.model_env else None
        default = env_model or self.spec.default_model

        if self.spec.alias_env:
            alias = get_env(self.spec.alias_env)
            if alias:
                self._model = alias
                self._resolved = True
                return self._model

        if self.spec.model_patterns and self.client() is not None:
            try:
                picked = await resolve_model_cached(
                    self.key, self.list_models, self.spec.model_patterns, default
                )
                self._model = picked or default
            except Exception:
                self._model = default
        else:
            self._model = default

        self._resolved = True
        return self._model

    # -- abstract vendor implementation ---------------------------------------
    @abstractmethod
    def _build_client(self):
        """Construct the vendor SDK client from secrets, or return None."""

    @abstractmethod
    async def generate(self, system: str, user: str, max_tokens: int) -> str:
        """Return the model's text completion for the given prompt."""

    async def list_models(self) -> Optional[List[str]]:
        """Return available model ids, or None if the vendor has no listing."""
        return None
