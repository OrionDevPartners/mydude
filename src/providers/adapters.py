"""Concrete LLM provider adapters.

Each adapter implements the vendor-agnostic ``LLMAdapter`` contract. Adding a
new provider = add an adapter here + register it in ``registry.py`` + add a
``[providers.<key>]`` block in config/providers.toml. No other code changes.
"""
import asyncio
import socket
from typing import List, Optional
from urllib.parse import urlparse

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
        api_key = self.primary_secret_value()
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
        api_key = self.primary_secret_value()
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

    async def list_models(self) -> Optional[List[str]]:
        """Live model catalogue for autorotation (newest Opus/Sonnet in lineage).

        The Anthropic SDK exposes ``client.models.list()``; older SDKs may not,
        so any failure degrades gracefully to ``None`` (the resolver then falls
        back to the cached/alias/default model). No model id is hardcoded here.
        """
        client = self.client()
        if client is None:
            return None
        try:
            r = await client.models.list(limit=1000)
            return [m.id for m in getattr(r, "data", []) if getattr(m, "id", None)]
        except Exception:
            return None


class GeminiGenerateAdapter(LLMAdapter):
    """Google Gemini generate_content API."""

    def _build_client(self):
        if genai is None:
            return None
        api_key = self.primary_secret_value()
        if not api_key:
            return None
        genai.configure(api_key=api_key)
        return genai

    # Bound every inference call so a stalled request fails loud instead of
    # hanging a caller. The SDK/GAPIC default is 600s plus a ServiceUnavailable
    # retry (10-20 min in practice); 180s is well above our worst real call
    # (~130s) yet terminates the underlying gRPC transport (raising
    # DeadlineExceeded), which cancelling the awaiting coroutine alone cannot do.
    REQUEST_TIMEOUT_S = 180

    async def generate(self, system: str, user: str, max_tokens: int) -> str:
        if self.client() is None:
            raise RuntimeError("gemini client unavailable")
        model = genai.GenerativeModel(self._model, system_instruction=system)
        r = await asyncio.to_thread(
            model.generate_content, user,
            request_options={"timeout": self.REQUEST_TIMEOUT_S},
        )
        text = self._extract_text(r)
        if not text:
            # Fail loud rather than silently returning empty: a thinking / pro
            # model can finish with a safety block or a thought-only candidate,
            # and an empty synthesis must surface, not masquerade as a result.
            raise RuntimeError(
                "gemini returned no text (%s)"
                % (self._blocked_reason(r) or "empty response")
            )
        return text.strip()

    @staticmethod
    def _extract_text(r) -> str:
        """Robustly assemble answer text across SDK response shapes.

        The ``.text`` quick-accessor raises on multi-part / thinking responses
        (e.g. a thought part alongside the answer), so prefer walking the
        candidate parts and fall back to ``.text`` only if that yields nothing.
        """
        try:
            answer: List[str] = []
            any_text: List[str] = []
            for cand in (getattr(r, "candidates", None) or []):
                content = getattr(cand, "content", None)
                for part in (getattr(content, "parts", None) or []):
                    t = getattr(part, "text", None)
                    if not t:
                        continue
                    any_text.append(t)
                    if not getattr(part, "thought", False):
                        answer.append(t)
            chosen = answer or any_text
            if chosen:
                return "".join(chosen)
        except Exception:
            pass
        try:
            t = getattr(r, "text", None)
            if t:
                return t
        except Exception:
            pass
        return ""

    @staticmethod
    def _blocked_reason(r) -> str:
        """Best-effort human-readable reason a response carried no text."""
        try:
            pf = getattr(r, "prompt_feedback", None)
            br = getattr(pf, "block_reason", None)
            if br:
                return "prompt blocked: %s" % br
            cands = getattr(r, "candidates", None) or []
            if cands:
                fr = getattr(cands[0], "finish_reason", None)
                if fr:
                    return "finish_reason=%s" % fr
        except Exception:
            pass
        return ""

    async def list_models(self) -> Optional[List[str]]:
        if self.client() is None:
            return None
        ms = await asyncio.to_thread(genai.list_models)
        return [m.name for m in ms]


def _server_listening(base_url: str, timeout: float = 0.3) -> bool:
    """TCP probe: True if something is listening on the base_url host:port.

    Local inference servers (Ollama, mlx_lm) are only sometimes running. Gating
    availability on a cheap socket connect keeps a dead local box from poisoning
    the swarm fanout, while staying synchronous (no event-loop blocking beyond
    the timeout) for use inside ``is_available``.

    When the local model server is reached over Cloudflare Mesh (a remote 100.96.x.x
    address) rather than localhost, the round-trip is higher. Use a larger timeout
    via the provider's ``probe_timeout_env`` / ``default_probe_timeout`` config, or
    the shared ``LOCAL_PROBE_TIMEOUT`` env var, rather than assuming sub-millisecond
    localhost latency.
    """
    try:
        parsed = urlparse(base_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


class _LocalOpenAICompatAdapter(OpenAIChatAdapter):
    """Base for local, OpenAI-compatible inference servers (no API key needed).

    Subclasses (Ollama, MLX) only differ by their default base URL and the
    registry ``provider`` name used to resolve a default model. Availability is
    gated on the local server actually listening, so when it is down the swarm
    transparently falls back to whatever cloud providers remain — and when all
    cloud providers are unavailable the ladder degrades to these instead of
    refusing (exec_locus=local, the local_degraded tier).

    Cloudflare Mesh support
    -----------------------
    Set the provider's base_url_env to a Mesh IP (e.g.
    ``OLLAMA_BASE_URL=http://100.96.0.1:11434/v1``) to reach a local model node
    enrolled as a Cloudflare Mesh peer. The probe timeout must be relaxed for
    cross-network latency — set ``OLLAMA_PROBE_TIMEOUT`` (or the shared
    ``LOCAL_PROBE_TIMEOUT``) to a value like ``2.0`` seconds instead of the
    localhost default. See docs/cloudflare-mesh-local-llm.md for full setup.
    """

    #: provider key in the local model registry (config key mirrors this)
    REGISTRY_PROVIDER = ""
    #: default endpoint when no base_url is configured in env_1/env_2
    DEFAULT_BASE_URL = ""
    #: placeholder key — local servers ignore it but the SDK requires one
    PLACEHOLDER_KEY = "local"
    #: env var name for per-provider probe timeout override (set in subclass)
    PROBE_TIMEOUT_ENV = ""
    #: default TCP probe timeout in seconds (localhost-tuned; override for Mesh)
    DEFAULT_PROBE_TIMEOUT = 0.5

    def _probe_timeout(self) -> float:
        """Return the TCP probe timeout for this provider.

        Resolution order:
          1. Provider-specific env var (e.g. OLLAMA_PROBE_TIMEOUT)
          2. Shared LOCAL_PROBE_TIMEOUT env var (applies to all local providers)
          3. DEFAULT_PROBE_TIMEOUT class default

        Set to 2.0+ when reaching a node over Cloudflare Mesh.
        """
        import os
        if self.PROBE_TIMEOUT_ENV:
            val = os.environ.get(self.PROBE_TIMEOUT_ENV, "").strip()
            if val:
                try:
                    return float(val)
                except ValueError:
                    pass
        shared = os.environ.get("LOCAL_PROBE_TIMEOUT", "").strip()
        if shared:
            try:
                return float(shared)
            except ValueError:
                pass
        return self.DEFAULT_PROBE_TIMEOUT

    def _base_url(self) -> str:
        configured = None
        if self.spec.base_url_env or self.spec.default_base_url:
            configured = get_env(self.spec.base_url_env, self.spec.default_base_url)
        return configured or self.DEFAULT_BASE_URL

    def _build_client(self):
        if AsyncOpenAI is None:
            return None
        api_key = (
            get_secret(self.spec.secrets[0]) if self.spec.secrets else None
        ) or self.PLACEHOLDER_KEY
        return AsyncOpenAI(api_key=api_key, base_url=self._base_url())

    #: how long a server-up probe result is trusted before re-probing (seconds)
    PROBE_TTL = 5.0

    def is_available(self) -> bool:
        # No secret to require; availability == client built AND server is up.
        # The TCP probe is cached for PROBE_TTL so the several is_available()
        # calls per task (and pure-cloud tasks that never use local) don't each
        # block the event loop on a socket connect (even a Mesh one).
        if self.client() is None:
            return False
        import time

        now = time.monotonic()
        last = getattr(self, "_probe_ts", None)
        if last is None or (now - last) > self.PROBE_TTL:
            self._probe_up = _server_listening(self._base_url(), timeout=self._probe_timeout())
            self._probe_ts = now
        return self._probe_up

    async def resolve_model(self) -> str:
        if self._resolved:
            return self._model
        env_model = get_env(self.spec.model_env) if self.spec.model_env else None
        if env_model:
            self._model = env_model
        else:
            try:
                from src.providers.local_registry import default_model_for_provider

                self._model = (
                    default_model_for_provider(
                        self.REGISTRY_PROVIDER, self.spec.default_model
                    )
                    or self.spec.default_model
                )
            except Exception:
                self._model = self.spec.default_model
        self._resolved = True
        return self._model


class OllamaAdapter(_LocalOpenAICompatAdapter):
    """Ollama local inference via its OpenAI-compatible API (localhost:11434/v1).

    To reach an Ollama node over Cloudflare Mesh, set:
      OLLAMA_BASE_URL=http://<mesh-ip>:11434/v1
      OLLAMA_PROBE_TIMEOUT=2.0   (or LOCAL_PROBE_TIMEOUT=2.0 for all local providers)
    """

    REGISTRY_PROVIDER = "ollama"
    DEFAULT_BASE_URL = "http://localhost:11434/v1"
    PLACEHOLDER_KEY = "ollama"
    PROBE_TIMEOUT_ENV = "OLLAMA_PROBE_TIMEOUT"


class MLXAdapter(_LocalOpenAICompatAdapter):
    """Apple MLX local inference via the mlx_lm OpenAI-compatible server
    (localhost:11435/v1).

    To reach an MLX node over Cloudflare Mesh, set:
      MLX_BASE_URL=http://<mesh-ip>:11435/v1
      MLX_PROBE_TIMEOUT=2.0   (or LOCAL_PROBE_TIMEOUT=2.0 for all local providers)
    """

    REGISTRY_PROVIDER = "mlx"
    DEFAULT_BASE_URL = "http://localhost:11435/v1"
    PLACEHOLDER_KEY = "mlx"
    PROBE_TIMEOUT_ENV = "MLX_PROBE_TIMEOUT"
