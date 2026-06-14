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
    # Alternative env_2 secret NAMES that satisfy the *primary* credential when
    # the canonical name (secrets[0]) is absent. Honors the "separate provider
    # from secrets" pillar: the same key works whether the operator stored it as
    # GEMINI_API_KEY, GOOGLE_API_KEY, google_ai_studio, etc. First present wins.
    secret_fallbacks: List[str] = field(default_factory=list)
    # Jurisdiction / data-residency locus this provider's models execute in
    # (in_azure | anthropic_hosted | provider_hosted | local). Read by the
    # jurisdiction router and the model-promotion gate to enforce exec_locus pins
    # and the cloud_shift kill switch. Declared per-provider in env_1.
    exec_locus: str = "in_azure"
    # Human-readable specialist role for this provider, surfaced in the UI/run
    # metadata (e.g. "Architecture, security review, long-context reasoning").
    specialty: str = ""
    # Benchmark-aware routing profile: category -> strength in [0, 1]. Used by
    # src/swarm/benchmark_routing.py to pick a *lead* provider per task category
    # (coding, reasoning, frontend_uiux, ...) and bias the governed judge
    # weighting. Operator-tunable in env_1; never hardcoded at a call site.
    benchmark_profile: dict = field(default_factory=dict)


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
    def _primary_secret_candidates(self) -> List[str]:
        """Candidate NAMES for the primary credential: the canonical name
        (secrets[0]) plus any configured fallbacks, in priority order."""
        names: List[str] = []
        if self.spec.secrets:
            names.append(self.spec.secrets[0])
        names.extend(self.spec.secret_fallbacks)
        return names

    def primary_secret_name(self) -> Optional[str]:
        """First present candidate name for the primary credential, else None."""
        for name in self._primary_secret_candidates():
            if has_secret(name):
                return name
        return None

    def primary_secret_value(self) -> Optional[str]:
        """Value of the first present primary-credential name, else None."""
        name = self.primary_secret_name()
        return get_secret(name) if name else None

    def secrets_present(self) -> bool:
        # Local/keyless providers (no secrets, no fallbacks) are always present.
        if not self.spec.secrets and not self.spec.secret_fallbacks:
            return True
        # The primary credential may be satisfied by any fallback name.
        if self.spec.secrets and self.primary_secret_name() is None:
            return False
        # Any *additional* required secrets must be present by their exact name.
        return all(has_secret(s) for s in self.spec.secrets[1:])

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
