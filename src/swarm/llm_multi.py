import os
import asyncio
import random
import time
from dataclasses import dataclass
from typing import Dict, Any, Optional, List

try:
    from openai import AsyncOpenAI
except Exception:
    AsyncOpenAI = None

try:
    import anthropic
except Exception:
    anthropic = None

try:
    import google.generativeai as genai
except Exception:
    genai = None

from src.swarm.model_resolver import resolve_models
from src.selfheal.circuit_breaker import CircuitBreaker
from src.swarm.compliance import analyze_agent_output, compute_effective_weight, classify_novelty, consensus_confidence_boost, NoveltyClassification
from src.swarm.hallucination import (
    build_features_from_compliance, compute_hallucination_risk,
    get_control_action, get_risk_tier, RiskTier
)
from src.swarm.constitution import CONSTITUTION_RULES, validate_language


@dataclass
class ProviderReply:
    provider: str
    model: str
    text: str
    ok: bool
    error: Optional[str] = None
    compliance_score: int = 100
    hallucination_risk: float = 0.0


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


class RateLimiter:
    def __init__(self):
        self.sems = {
            "openai": asyncio.Semaphore(_env_int("OPENAI_CONCURRENCY", 4)),
            "anthropic": asyncio.Semaphore(_env_int("ANTHROPIC_CONCURRENCY", 3)),
            "gemini": asyncio.Semaphore(_env_int("GEMINI_CONCURRENCY", 3)),
            "grok": asyncio.Semaphore(_env_int("GROK_CONCURRENCY", 3)),
        }

    def sem(self, provider: str) -> asyncio.Semaphore:
        return self.sems.get(provider, asyncio.Semaphore(2))


async def _backoff_retry(fn, max_tries=4):
    for attempt in range(max_tries):
        try:
            return await fn()
        except Exception as e:
            if attempt == max_tries - 1:
                raise
            await asyncio.sleep((0.6 * (2 ** attempt)) + random.random() * 0.25)


class MultiProviderLLM:
    def __init__(self):
        self.limiter = RateLimiter()
        self.circuit_breaker = CircuitBreaker()

        self.openai_key = os.getenv("OPENAI_API_KEY")
        self.anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        self.grok_key = os.getenv("GROK_API_KEY")

        self.openai_model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        self.anthropic_model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        self.grok_model = os.getenv("GROK_MODEL", "grok-2-latest")

        self.budget_tokens = _env_int("PROVIDER_BUDGET_TOKENS", 1200)

        self._openai = AsyncOpenAI(api_key=self.openai_key) if (AsyncOpenAI and self.openai_key) else None
        self._anthropic = anthropic.AsyncAnthropic(api_key=self.anthropic_key) if (anthropic and self.anthropic_key) else None

        if genai and self.gemini_key:
            genai.configure(api_key=self.gemini_key)

        self.grok_base_url = os.getenv("GROK_BASE_URL", "https://api.x.ai/v1")
        self._grok = (
            AsyncOpenAI(
                api_key=self.grok_key,
                base_url=self.grok_base_url,
            )
            if (AsyncOpenAI and self.grok_key)
            else None
        )

        self._resolved = False

    def available(self) -> Dict[str, bool]:
        return {
            "openai": bool(self._openai),
            "anthropic": bool(self._anthropic),
            "gemini": bool(genai and self.gemini_key),
            "grok": bool(self._grok),
        }

    async def _resolve_once(self):
        if self._resolved:
            return

        openai_list = None
        gemini_list = None
        grok_list = None

        if self._openai:
            async def _ol():
                r = await self._openai.models.list()
                return [m.id for m in r.data]
            openai_list = _ol

        if genai and self.gemini_key:
            async def _gl():
                ms = await asyncio.to_thread(genai.list_models)
                return [m.name for m in ms]
            gemini_list = _gl

        if self._grok:
            async def _xl():
                r = await self._grok.models.list()
                return [m.id for m in r.data]
            grok_list = _xl

        try:
            resolved = await resolve_models(
                openai_list_models=openai_list,
                anthropic_alias=os.getenv("ANTHROPIC_OPUS_ALIAS"),
                gemini_list_models=gemini_list,
                grok_list_models=grok_list,
            )
            self.openai_model = resolved.openai
            self.anthropic_model = resolved.anthropic
            self.gemini_model = resolved.gemini
            self.grok_model = resolved.grok
        except Exception:
            pass

        self._resolved = True

    def score_replies(self, replies: List[ProviderReply]) -> List[ProviderReply]:
        for r in replies:
            if not r.ok or not r.text:
                continue
            try:
                report = analyze_agent_output(r.text, intent_refs=[], mode="ANALYTIC")
                r.compliance_score = report.score
                features = build_features_from_compliance(report, replies, 0.0)
                r.hallucination_risk = compute_hallucination_risk(features)
            except Exception:
                pass
        try:
            ok_texts = [r.text for r in replies if r.ok and r.text]
            for r in replies:
                if not r.ok or not r.text:
                    continue
                try:
                    novelty = classify_novelty(r.text)
                    if novelty != NoveltyClassification.STANDARD:
                        boost = consensus_confidence_boost(ok_texts, r.text, threshold=3)
                        if boost["boosted"]:
                            r.compliance_score = min(100, r.compliance_score + 15)
                except Exception:
                    pass
        except Exception:
            pass
        return replies

    async def call_team(
        self,
        system: str,
        user: str,
        roles_hint: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        await self._resolve_once()
        roles_hint = roles_hint or {}
        replies = await self._fanout(system, user, roles_hint)
        try:
            replies = self.score_replies(replies)
        except Exception:
            pass
        merged = await self._judge_merge(system, user, replies)
        return {
            "replies": replies,
            "merged": merged,
            "compliance_scores": {r.provider: r.compliance_score for r in replies if r.ok},
            "hallucination_risks": {r.provider: r.hallucination_risk for r in replies if r.ok},
        }

    async def _fanout(self, system: str, user: str, roles_hint: Dict[str, str]) -> List[ProviderReply]:
        tasks = []
        if self._openai and await self.circuit_breaker.can_call("openai"):
            tasks.append(self._call_openai(system, user, roles_hint.get("openai")))
        if self._anthropic and await self.circuit_breaker.can_call("anthropic"):
            tasks.append(self._call_anthropic(system, user, roles_hint.get("anthropic")))
        if genai and self.gemini_key and await self.circuit_breaker.can_call("gemini"):
            tasks.append(self._call_gemini(system, user, roles_hint.get("gemini")))
        if self._grok and await self.circuit_breaker.can_call("grok"):
            tasks.append(self._call_grok(system, user, roles_hint.get("grok")))

        if not tasks:
            return [ProviderReply("none", "none", "No providers configured. Add API keys.", False, "no_providers")]

        return await asyncio.gather(*tasks)

    async def _call_openai(self, system: str, user: str, hint: Optional[str]) -> ProviderReply:
        async with self.limiter.sem("openai"):
            async def run():
                msg = user if not hint else f"[Specialization: {hint}]\n{user}"
                r = await self._openai.chat.completions.create(
                    model=self.openai_model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": msg}],
                    max_tokens=self.budget_tokens,
                )
                return r.choices[0].message.content or ""
            try:
                t0 = time.time()
                text = await _backoff_retry(run)
                await self.circuit_breaker.record_success("openai", time.time() - t0)
                return ProviderReply("openai", self.openai_model, text, True)
            except Exception as e:
                await self.circuit_breaker.record_failure("openai", str(e))
                return ProviderReply("openai", self.openai_model, "", False, str(e))

    async def _call_anthropic(self, system: str, user: str, hint: Optional[str]) -> ProviderReply:
        async with self.limiter.sem("anthropic"):
            async def run():
                msg = user if not hint else f"[Specialization: {hint}]\n{user}"
                r = await self._anthropic.messages.create(
                    model=self.anthropic_model,
                    max_tokens=self.budget_tokens,
                    system=system,
                    messages=[{"role": "user", "content": msg}],
                )
                parts = []
                for b in r.content:
                    if getattr(b, "type", None) == "text":
                        parts.append(b.text)
                return "\n".join(parts).strip()
            try:
                t0 = time.time()
                text = await _backoff_retry(run)
                await self.circuit_breaker.record_success("anthropic", time.time() - t0)
                return ProviderReply("anthropic", self.anthropic_model, text, True)
            except Exception as e:
                await self.circuit_breaker.record_failure("anthropic", str(e))
                return ProviderReply("anthropic", self.anthropic_model, "", False, str(e))

    async def _call_gemini(self, system: str, user: str, hint: Optional[str]) -> ProviderReply:
        async with self.limiter.sem("gemini"):
            async def run():
                msg = user if not hint else f"[Specialization: {hint}]\n{user}"
                model = genai.GenerativeModel(self.gemini_model, system_instruction=system)
                r = await asyncio.to_thread(model.generate_content, msg)
                return (getattr(r, "text", "") or "").strip()
            try:
                t0 = time.time()
                text = await _backoff_retry(run)
                await self.circuit_breaker.record_success("gemini", time.time() - t0)
                return ProviderReply("gemini", self.gemini_model, text, True)
            except Exception as e:
                await self.circuit_breaker.record_failure("gemini", str(e))
                return ProviderReply("gemini", self.gemini_model, "", False, str(e))

    async def _call_grok(self, system: str, user: str, hint: Optional[str]) -> ProviderReply:
        async with self.limiter.sem("grok"):
            async def run():
                msg = user if not hint else f"[Specialization: {hint}]\n{user}"
                r = await self._grok.chat.completions.create(
                    model=self.grok_model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": msg}],
                    max_tokens=self.budget_tokens,
                )
                return r.choices[0].message.content or ""
            try:
                t0 = time.time()
                text = await _backoff_retry(run)
                await self.circuit_breaker.record_success("grok", time.time() - t0)
                return ProviderReply("grok", self.grok_model, text, True)
            except Exception as e:
                await self.circuit_breaker.record_failure("grok", str(e))
                return ProviderReply("grok", self.grok_model, "", False, str(e))

    async def _judge_merge(self, system: str, user: str, replies: List[ProviderReply]) -> str:
        chunks = []
        weights = {}
        for r in replies:
            status = "OK" if r.ok else f"ERR({r.error})"
            chunks.append(f"### {r.provider}/{r.model} [{status}] [CS={r.compliance_score}, HR={r.hallucination_risk:.2f}]\n{r.text[:6000]}")
            try:
                weights[r.provider] = compute_effective_weight(1.0, r.compliance_score, 1.0 if r.ok else 0.0)
            except Exception:
                weights[r.provider] = 1.0 if r.ok else 0.0
            try:
                novelty = classify_novelty(r.text) if r.ok else NoveltyClassification.STANDARD
                if novelty != NoveltyClassification.STANDARD and r.ok:
                    weights[r.provider] = compute_effective_weight(1.0, r.compliance_score, 1.0 if r.ok else 0.0, novelty_bonus=0.3)
            except Exception:
                pass
        debate = "\n\n".join(chunks)

        has_critical = False
        try:
            has_critical = any(
                get_risk_tier(r.hallucination_risk) == RiskTier.CRITICAL
                for r in replies if r.ok
            )
        except Exception:
            pass

        critical_warning = ""
        if has_critical:
            critical_warning = "\nWARNING: Critical hallucination risk detected. Require evidence for all claims. Downgrade unverified assertions.\n"

        judge_prompt = (
            "You are the MERGER/JUDGE.\n\n"
            "Goal:\n"
            "Synthesize the providers' outputs into one best answer with:\n"
            "- high correctness\n"
            "- strong security posture\n"
            "- concrete, actionable steps\n"
            "- minimal token bloat\n\n"
            "Each provider's output includes compliance score (CS) and hallucination risk (HR).\n"
            "WEIGHT providers by compliance score. Higher CS = more trustworthy. Reject claims from providers with CS < 65.\n"
            "NOVEL HYPOTHESES: Do NOT reject novel ideas or creative theories just because they lack traditional evidence.\n"
            "If 3+ providers converge on a novel concept, treat it as HIGH CONFIDENCE. Innovation lives in the edges.\n"
            f"{critical_warning}"
            "Return ONLY the final consolidated worker-format response:\n"
            "RESULT:\nARTIFACTS:\nCHECKS:\nRISKS:\nCAPABILITIES:\nCOMPRESSED_HANDOFF:\n\n"
            f"User request:\n{user}\n\n"
            f"Providers' outputs:\n{debate}"
        )

        if self._openai:
            try:
                r = await self._openai.chat.completions.create(
                    model=self.openai_model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": judge_prompt}],
                    max_tokens=self.budget_tokens,
                )
                return r.choices[0].message.content or ""
            except Exception:
                pass

        if self._anthropic:
            try:
                r = await self._anthropic.messages.create(
                    model=self.anthropic_model,
                    max_tokens=self.budget_tokens,
                    system=system,
                    messages=[{"role": "user", "content": judge_prompt}],
                )
                parts = []
                for b in r.content:
                    if getattr(b, "type", None) == "text":
                        parts.append(b.text)
                return "\n".join(parts).strip()
            except Exception:
                pass

        return debate[:7000]
