"""Concrete LLM provider adapters.

Each adapter implements the vendor-agnostic ``LLMAdapter`` contract. Adding a
new provider = add an adapter here + register it in ``registry.py`` + add a
``[providers.<key>]`` block in config/providers.toml. No other code changes.
"""
import asyncio
from typing import List, Optional

from src.providers.base import LLMAdapter
from src.providers.secrets import get_secret, get_env

try:
    from openai import AsyncOpenAI
except Exception:  # pragma: no cover - optional dependency guard
    AsyncOpenAI = None

try:
    import anthropic
except Exception:  # pragma: no cover
    anthropic = None

try:
    import google.generativeai as genai
except Exception:  # pragma: no cover
    genai = None


class OpenAIChatAdapter(LLMAdapter):
    """OpenAI-compatible Chat Completions API.

    Works for OpenAI and any OpenAI-compatible endpoint (e.g. xAI Grok) by
    pointing ``base_url_env`` / ``default_base_url`` at the alternate host.
    """

    def _build_client(self):
        if AsyncOpenAI is None:
            return None
        api_key = get_secret(self.spec.secrets[0]) if self.spec.secrets else None
        if not api_key:
            return None
        kwargs = {"api_key": api_key}
        base_url = None
        if self.spec.base_url_env or self.spec.default_base_url:
            base_url = get_env(self.spec.base_url_env, self.spec.default_base_url)
        if base_url:
            kwargs["base_url"] = base_url
        return AsyncOpenAI(**kwargs)

    async def generate(self, system: str, user: str, max_tokens: int) -> str:
        client = self.client()
        r = await client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
        )
        return r.choices[0].message.content or ""

    async def list_models(self) -> Optional[List[str]]:
        client = self.client()
        if client is None:
            return None
        r = await client.models.list()
        return [m.id for m in r.data]


class AnthropicMessagesAdapter(LLMAdapter):
    """Anthropic Messages API."""

    def _build_client(self):
        if anthropic is None:
            return None
        api_key = get_secret(self.spec.secrets[0]) if self.spec.secrets else None
        if not api_key:
            return None
        return anthropic.AsyncAnthropic(api_key=api_key)

    async def generate(self, system: str, user: str, max_tokens: int) -> str:
        client = self.client()
        r = await client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        parts = [b.text for b in r.content if getattr(b, "type", None) == "text"]
        return "\n".join(parts).strip()


class GeminiGenerateAdapter(LLMAdapter):
    """Google Gemini generate_content API."""

    def _build_client(self):
        if genai is None:
            return None
        api_key = get_secret(self.spec.secrets[0]) if self.spec.secrets else None
        if not api_key:
            return None
        genai.configure(api_key=api_key)
        return genai

    async def generate(self, system: str, user: str, max_tokens: int) -> str:
        if self.client() is None:
            raise RuntimeError("gemini client unavailable")
        model = genai.GenerativeModel(self._model, system_instruction=system)
        r = await asyncio.to_thread(model.generate_content, user)
        return (getattr(r, "text", "") or "").strip()

    async def list_models(self) -> Optional[List[str]]:
        if self.client() is None:
            return None
        ms = await asyncio.to_thread(genai.list_models)
        return [m.name for m in ms]
