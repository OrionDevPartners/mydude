import asyncio
import random
import time
from dataclasses import dataclass
from typing import Dict, Any, Optional, List

from src.providers.config import llm_provider_specs
from src.providers.registry import build_adapter
from src.providers.secrets import get_env
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
        val = get_env(name)
        return int(val) if val is not None else default
    except Exception:
        return default


class RateLimiter:
    def __init__(self, specs):
        self.sems = {}
        for s in specs:
            n = _env_int(s.concurrency_env, s.default_concurrency) if s.concurrency_env else s.default_concurrency
            self.sems[s.key] = asyncio.Semaphore(n)

    def sem(self, key: str) -> asyncio.Semaphore:
        return self.sems.get(key, asyncio.Semaphore(2))


async def _backoff_retry(fn, max_tries=4):
    for attempt in range(max_tries):
        try:
            return await fn()
        except Exception as e:
            if attempt == max_tries - 1:
                raise
            await asyncio.sleep((0.6 * (2 ** attempt)) + random.random() * 0.25)


class MultiProviderLLM:
    """Provider-agnostic LLM swarm.

    The set of providers, their models, concurrency and role hints all come from
    env_1 (config/providers.toml) via adapters. This class names no vendor.
    """

    def __init__(self):
        self.specs = llm_provider_specs()
        self.adapters = [build_adapter(s) for s in self.specs]
        self.limiter = RateLimiter(self.specs)
        self.circuit_breaker = CircuitBreaker()
        self.budget_tokens = _env_int("PROVIDER_BUDGET_TOKENS", 1200)
        self._resolved = False

    def _available_adapters(self):
        return [a for a in self.adapters if a.is_available()]

    def available(self) -> Dict[str, bool]:
        return {a.key: a.is_available() for a in self.adapters}

    async def _resolve_once(self):
        if self._resolved:
            return
        await asyncio.gather(
            *[a.resolve_model() for a in self._available_adapters()],
            return_exceptions=True,
        )
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
        for adapter in self._available_adapters():
            if await self.circuit_breaker.can_call(adapter.key):
                hint = roles_hint.get(adapter.key, adapter.role_hint)
                tasks.append(self._call(adapter, system, user, hint))

        if not tasks:
            return [ProviderReply("none", "none", "No providers configured. Add API keys.", False, "no_providers")]

        return await asyncio.gather(*tasks)

    async def _call(self, adapter, system: str, user: str, hint: Optional[str]) -> ProviderReply:
        async with self.limiter.sem(adapter.key):
            async def run():
                msg = user if not hint else f"[Specialization: {hint}]\n{user}"
                return await adapter.generate(system, msg, self.budget_tokens)
            try:
                t0 = time.time()
                text = await _backoff_retry(run)
                await self.circuit_breaker.record_success(adapter.key, time.time() - t0)
                return ProviderReply(adapter.key, adapter.model, text, True)
            except Exception as e:
                await self.circuit_breaker.record_failure(adapter.key, str(e))
                return ProviderReply(adapter.key, adapter.model, "", False, str(e))

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

        for adapter in self._available_adapters():
            try:
                return await adapter.generate(system, judge_prompt, self.budget_tokens)
            except Exception:
                continue

        return debate[:7000]
