"""Adapter registry — maps the ``adapter`` name declared in env_1 to a concrete
implementation. Register a new provider implementation here once; selection and
configuration happen entirely in config/providers.toml.
"""
from src.providers.base import LLMAdapter, ProviderSpec
from src.providers.adapters import (
    OpenAIChatAdapter,
    AnthropicMessagesAdapter,
    GeminiGenerateAdapter,
    OllamaAdapter,
    MLXAdapter,
)


class UnknownAdapterError(RuntimeError):
    """Raised when env_1 references an adapter that is not registered."""


ADAPTER_REGISTRY = {
    "openai_chat": OpenAIChatAdapter,
    "anthropic_messages": AnthropicMessagesAdapter,
    "gemini_generate": GeminiGenerateAdapter,
    "ollama_chat": OllamaAdapter,
    "mlx_chat": MLXAdapter,
}


def build_adapter(spec: ProviderSpec) -> LLMAdapter:
    cls = ADAPTER_REGISTRY.get(spec.adapter)
    if cls is None:
        raise UnknownAdapterError(
            "Unknown adapter '%s' for provider '%s'. Registered adapters: %s"
            % (spec.adapter, spec.key, ", ".join(sorted(ADAPTER_REGISTRY)))
        )
    return cls(spec)
