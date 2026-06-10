import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

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


_JUDGE_DEGRADED_BANNER = (
    "[DEGRADED / UNVERIFIED SYNTHESIS] The governed judge program could not run; "
    "this answer was produced from the live, governance-approved prompt via a raw "
    "provider call and did NOT complete full governance scoring. Treat with caution.\n\n"
)


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
        # Jurisdiction routing state (set by the orchestrator before a run).
        # exec_locus_pin: "any" allows all loci; otherwise only providers whose
        # exec_locus matches the pin are eligible. cloud_shift_active=False is the
        # kill switch — all non-local providers are dropped.
        self.exec_locus_pin = "any"
        self.cloud_shift_active = True

    def apply_jurisdiction(self, exec_locus_pin: str = "any", cloud_shift_active: bool = True) -> None:
        """Pin provider selection to a jurisdiction decision before a run."""
        self.exec_locus_pin = exec_locus_pin or "any"
        self.cloud_shift_active = bool(cloud_shift_active)

    def _passes_jurisdiction(self, adapter) -> bool:
        from src.swarm.jurisdiction import get_exec_locus
        locus = get_exec_locus(adapter.key)
        is_local = locus == "local"
        # Kill switch: no cloud egress -> only local providers survive.
        if not self.cloud_shift_active and not is_local:
            return False
        # exec_locus pin: when pinned, only matching providers survive.
        if self.exec_locus_pin not in ("any", "", None):
            if self.exec_locus_pin == "local":
                return is_local
            return locus == self.exec_locus_pin
        return True

    def _available_adapters(self):
        return [a for a in self.adapters if a.is_available() and self._passes_jurisdiction(a)]

    def effective_routing(self):
        """Return (fallback_tier, exec_locus, outcome) for the current state.

        Reflects the fallback ladder actually in effect after jurisdiction
        filtering: preferred (1) when cloud providers are routable, local_degraded
        (4) when only local survive (or the kill switch is on), refuse (5) when
        nothing is routable.
        """
        from src.swarm.jurisdiction import get_exec_locus
        routable = self._available_adapters()
        if routable:
            loci = {get_exec_locus(a.key) for a in routable}
            if not self.cloud_shift_active or loci == {"local"}:
                return 4, "local", "degraded"
            if self.exec_locus_pin not in ("any", "", None):
                return 1, self.exec_locus_pin, "executed"
            return 1, next(iter(loci)), "executed"
        if not self.cloud_shift_active:
            return 5, "local", "refused"
        pin = self.exec_locus_pin if self.exec_locus_pin not in ("any", "", None) else "in_azure"
        return 5, pin, "refused"

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

        # Primary path: the governed, versioned, governance-APPROVED judge program
        # (DSPy). It fails loud internally (records a 'failed' trace + raises) when
        # no provider is available or the output can't be parsed.
        try:
            from src.promptopt.runtime import run_judge
            return await run_judge(user, debate, critical_warning, self.budget_tokens)
        except Exception as e:
            logger.warning(
                "Governed judge program unavailable (%s); attempting a DEGRADED "
                "synthesis with the LIVE approved prompt.", e,
            )

        # Degraded path: NEVER a hardcoded/divergent prompt, and NEVER a bypass of an
        # evolved live version. Re-run the SAME live, governance-approved instructions
        # via a raw provider call, mark the output degraded/unverified, and record a
        # 'degraded' trace so the bypass is explicit and audited (governance pillars
        # 1 & 4: no silent fallback to an unverified prompt; no ungoverned output).
        degraded = await self._degraded_judge_synthesis(user, debate, critical_warning)
        if degraded is not None:
            return degraded

        # Nothing reachable: fail loud in worker format. We refuse to emit the raw,
        # ungoverned provider debate as if it were a synthesized answer.
        logger.error(
            "Judge synthesis unavailable and no provider reachable; refusing to emit "
            "ungoverned provider output."
        )
        return (
            _JUDGE_DEGRADED_BANNER
            + "RESULT: Unable to produce a governed synthesis — no LLM provider is reachable.\n"
            + "ARTIFACTS: none\n"
            + "CHECKS: governed judge program failed AND the degraded path could not reach any provider\n"
            + "RISKS: raw provider output withheld to avoid emitting ungoverned content (governance pillar 4)\n"
            + "CAPABILITIES: restore an LLM provider to resume governed synthesis\n"
            + "COMPRESSED_HANDOFF: judge unavailable; no provider; output withheld by governance\n"
        )

    async def _degraded_judge_synthesis(self, user: str, debate: str, risk_directive: str):
        """Degraded merger/judge synthesis, used ONLY when the governed DSPy program
        cannot run. It uses the LIVE, governance-approved instructions (never a
        hardcoded copy, never a bypass of an evolved version) via a raw provider call,
        marks the result degraded/unverified, scores it with the governance analyzers,
        and records a 'degraded' trace (excluded from the optimizer trainset). Returns
        the marked text, or None if no provider is reachable."""
        try:
            from src.promptopt.specs import JUDGE_PROGRAM
            from src.promptopt import store
            version_id, instructions, _demos = store.get_live_instructions(JUDGE_PROGRAM)
        except Exception as e:
            logger.warning("Could not load live judge instructions for degraded synthesis: %s", e)
            return None
        if not (instructions or "").strip():
            return None

        prompt = (
            instructions + "\n\n"
            + (risk_directive or "")
            + "\nUser request:\n" + (user or "")
            + "\n\nProviders' outputs:\n" + (debate or "")
        )
        for adapter in self._available_adapters():
            try:
                text = await adapter.generate("", prompt, self.budget_tokens)
            except Exception:
                continue
            if not (text or "").strip():
                continue
            try:
                from src.promptopt.metric import score_text
                info = score_text(text)
                store.record_trace(
                    JUDGE_PROGRAM, version_id,
                    {"user_request": user, "provider_outputs": debate, "risk_directive": risk_directive},
                    text, score_info=info, status="degraded",
                    feedback={"reason": "governed DSPy judge failed; degraded raw call with live approved prompt"},
                )
            except Exception:
                pass
            return _JUDGE_DEGRADED_BANNER + text
        return None
