"""MyDude jurisdiction routing — nested jurisdiction ladder.

Implements the routing authority in agents_home:
  Tier 0: MyDude jurisdiction decision (agents_home)
  Tier 1: Foundry Model Router (within MyDude-granted Azure deployments only)
  Tier 2: LiteLLM (within MyDude-granted provider APIs only)

5-tier fallback:
  1. preferred_model
  2. cloud_shift (to another cloud provider within exec_locus)
  3. ssh_edge (SSH bridge to local machine)
  4. local_degraded (MLX/Ollama with reduced quality)
  5. refuse_or_queue

cloud_shift=false (kill switch) routes everything to local_degraded or refuse.

This module reconciles with the existing MyDude provider/broker/policy layer
(src/swarm/broker.py, src/swarm/policy.py) rather than replacing it. It
reads the exec_locus and cloud_shift state from agents_home at runtime and
injects those decisions into the existing PolicyEngine and CapabilityBroker.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger("mydude.routing.jurisdiction")


class ExecLocus(str, Enum):
    IN_AZURE = "in_azure"
    ANTHROPIC_HOSTED = "anthropic_hosted"
    LOCAL = "local"


class FallbackTier(int, Enum):
    PREFERRED = 1
    CLOUD_SHIFT = 2
    SSH_EDGE = 3
    LOCAL_DEGRADED = 4
    REFUSE_OR_QUEUE = 5


class Outcome(str, Enum):
    EXECUTED = "executed"
    REFUSED = "refused"
    QUEUED = "queued"
    DEGRADED = "degraded"


@dataclass
class JurisdictionDecision:
    exec_locus: ExecLocus
    fallback_tier: FallbackTier
    model_team: Optional[str]
    resolved_provider: Optional[str]
    resolved_model: Optional[str]
    cloud_shift_active: bool
    local_only: bool
    domain: str
    outcome: Outcome
    detail: dict = field(default_factory=dict)


class CloudShiftKillSwitch:
    """Reads cloud_shift state from agents_home.routing.cloud_shift.

    When cloud_shift=false, all cloud egress is disabled and the routing
    ladder is forced to local_degraded or refuse/queue.
    """

    def __init__(self, dsn: Optional[str] = None):
        self._dsn = dsn or os.environ.get("PG_AGENTS_HOME_DSN", "")
        self._cached: Optional[bool] = None

    def is_enabled(self) -> bool:
        """Return True if cloud egress is permitted."""
        if not self._dsn:
            logger.debug("PG_AGENTS_HOME_DSN not set; cloud_shift defaults to enabled.")
            return True
        try:
            import psycopg2
            conn = psycopg2.connect(self._dsn)
            with conn.cursor() as cur:
                cur.execute("SELECT enabled FROM routing.cloud_shift WHERE id = 1")
                row = cur.fetchone()
            conn.close()
            result = bool(row[0]) if row else True
            self._cached = result
            return result
        except Exception as e:
            logger.warning("Failed to read cloud_shift state: %s; defaulting to enabled.", e)
            return self._cached if self._cached is not None else True

    def set_enabled(self, value: bool, reason: str = "", updated_by: str = "system") -> None:
        """Set cloud_shift state in agents_home (requires agents_home_writer role)."""
        if not self._dsn:
            raise RuntimeError("PG_AGENTS_HOME_DSN not set; cannot update cloud_shift.")
        import psycopg2
        conn = psycopg2.connect(self._dsn)
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE routing.cloud_shift SET enabled=%s, reason=%s, updated_at=now(), updated_by=%s WHERE id=1",
                    (value, reason, updated_by),
                )
        conn.close()
        self._cached = value
        logger.info("cloud_shift set to %s by %s: %s", value, updated_by, reason)


class ModelTeamResolver:
    """Resolves model team assignments from agents_home.policy.model_team_policy."""

    def __init__(self, dsn: Optional[str] = None):
        self._dsn = dsn or os.environ.get("PG_AGENTS_HOME_DSN", "")

    def resolve(self, domain: str, exec_locus: ExecLocus, team: str = "default") -> list[dict]:
        """Return ordered list of allowed models for a domain+exec_locus+team."""
        if not self._dsn:
            return []
        try:
            import psycopg2
            conn = psycopg2.connect(self._dsn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT model_id, provider, exec_locus_pin, cost_cap_usd, latency_budget_ms
                    FROM policy.model_team_policy
                    WHERE team = %s
                      AND domain = %s
                      AND exec_locus_pin IN (%s, 'any')
                      AND allowed = TRUE
                    ORDER BY priority DESC
                    """,
                    (team, domain, exec_locus.value),
                )
                rows = cur.fetchall()
            conn.close()
            return [
                {
                    "model_id": r[0],
                    "provider": r[1],
                    "exec_locus_pin": r[2],
                    "cost_cap_usd": r[3],
                    "latency_budget_ms": r[4],
                }
                for r in rows
            ]
        except Exception as e:
            logger.warning("ModelTeamResolver.resolve failed: %s", e)
            return []


class JurisdictionRouter:
    """MyDude jurisdiction decision engine.

    Implements the nested routing ladder:
      MyDude ⊃ Foundry Model Router ⊃ LiteLLM

    This class makes the routing decision; the actual provider call is
    dispatched through the existing CapabilityBroker (src/swarm/broker.py).
    """

    def __init__(self, dsn: Optional[str] = None):
        self.cloud_shift = CloudShiftKillSwitch(dsn=dsn)
        self.model_teams = ModelTeamResolver(dsn=dsn)

    def _local_provider_candidates(self) -> list[dict]:
        """Local-provider candidates for the local_degraded tier.

        Used when agents_home has no policy rows for the local exec_locus (e.g.
        offline, or no PG_AGENTS_HOME_DSN configured). Reads exec_locus=local
        providers from config/providers.toml and pairs each with its installed
        model from the local model registry (falling back to the config default).
        This is what makes the router *select local providers* when
        cloud_shift=false / exec_locus_pin=local even without a policy DB.
        """
        candidates: list[dict] = []
        try:
            import tomllib
            from pathlib import Path

            cfg_path = Path(__file__).resolve().parents[3] / "config" / "providers.toml"
            with open(cfg_path, "rb") as f:
                cfg = tomllib.load(f)
        except Exception as e:
            logger.debug("local provider config read failed: %s", e)
            return candidates

        try:
            import sys
            root = Path(__file__).resolve().parents[3]
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            from src.providers.local_registry import default_model_for_provider

            _resolve = default_model_for_provider
        except Exception:
            _resolve = None  # type: ignore

        for key, prov in (cfg.get("providers", {}) or {}).items():
            if prov.get("exec_locus") != "local":
                continue
            default_model = prov.get("default_model", "")
            model_id = default_model
            if _resolve is not None:
                try:
                    model_id = _resolve(key, default_model) or default_model
                except Exception:
                    model_id = default_model
            candidates.append(
                {
                    "model_id": model_id,
                    "provider": key,
                    "exec_locus_pin": "local",
                    "cost_cap_usd": 0.0,
                    "latency_budget_ms": None,
                }
            )
        return candidates

    def decide(
        self,
        domain: str = "general",
        team: str = "default",
        exec_locus_override: Optional[str] = None,
        local_only: bool = False,
    ) -> JurisdictionDecision:
        """Make the jurisdiction routing decision for a request."""

        cloud_shift_active = self.cloud_shift.is_enabled()

        # Determine effective exec_locus
        if local_only or not cloud_shift_active:
            exec_locus = ExecLocus.LOCAL
        elif exec_locus_override:
            try:
                exec_locus = ExecLocus(exec_locus_override)
            except ValueError:
                exec_locus = ExecLocus.IN_AZURE
        else:
            exec_locus = ExecLocus.IN_AZURE

        # Resolve model team
        candidates = self.model_teams.resolve(domain, exec_locus, team)

        if not candidates and exec_locus != ExecLocus.LOCAL:
            # Tier 2 fallback: try cloud_shift (different exec_locus within cloud)
            logger.info("No candidates for %s/%s; attempting cloud_shift fallback.", domain, exec_locus.value)
            candidates = self.model_teams.resolve(domain, ExecLocus.ANTHROPIC_HOSTED, team)
            if candidates:
                exec_locus = ExecLocus.ANTHROPIC_HOSTED
                tier = FallbackTier.CLOUD_SHIFT
            else:
                tier = FallbackTier.SSH_EDGE
        elif not candidates:
            tier = FallbackTier.LOCAL_DEGRADED
        else:
            tier = FallbackTier.PREFERRED

        if not candidates:
            # Tier 4: local degraded. Prefer a policy-driven local team if one
            # exists; otherwise fall back to the exec_locus=local providers in
            # config/providers.toml (Ollama/MLX) so we degrade to local instead
            # of refusing when no policy DB is configured.
            local_candidates = self.model_teams.resolve(domain, ExecLocus.LOCAL, team)
            if not local_candidates:
                local_candidates = self._local_provider_candidates()
            if local_candidates:
                exec_locus = ExecLocus.LOCAL
                candidates = local_candidates
                tier = FallbackTier.LOCAL_DEGRADED
            else:
                # Tier 5: refuse or queue
                return JurisdictionDecision(
                    exec_locus=exec_locus,
                    fallback_tier=FallbackTier.REFUSE_OR_QUEUE,
                    model_team=team,
                    resolved_provider=None,
                    resolved_model=None,
                    cloud_shift_active=cloud_shift_active,
                    local_only=local_only,
                    domain=domain,
                    outcome=Outcome.REFUSED,
                    detail={"reason": "no_candidates_in_any_tier"},
                )

        # Pick best candidate (first in priority order from policy)
        best = candidates[0]

        return JurisdictionDecision(
            exec_locus=exec_locus,
            fallback_tier=tier,
            model_team=team,
            resolved_provider=best["provider"],
            resolved_model=best["model_id"],
            cloud_shift_active=cloud_shift_active,
            local_only=local_only or (exec_locus == ExecLocus.LOCAL),
            domain=domain,
            outcome=Outcome.EXECUTED,
            detail={"candidates_evaluated": len(candidates), "best_exec_locus_pin": best["exec_locus_pin"]},
        )

    def decide_for_existing_broker(
        self,
        domain: str = "general",
        team: str = "default",
    ) -> dict:
        """Return a routing hint dict for the existing CapabilityBroker.

        The existing broker/policy layer (src/swarm/broker.py) already handles
        capability gating. This method extends it with exec_locus + cloud_shift
        state without replacing the existing logic.

        Returns a dict the broker can merge into params before dispatch.
        """
        decision = self.decide(domain=domain, team=team)
        return {
            "_jurisdiction": {
                "exec_locus": decision.exec_locus.value,
                "fallback_tier": decision.fallback_tier.value,
                "resolved_provider": decision.resolved_provider,
                "resolved_model": decision.resolved_model,
                "cloud_shift_active": decision.cloud_shift_active,
                "local_only": decision.local_only,
                "domain": domain,
                "outcome": decision.outcome.value,
            }
        }


# ---------------------------------------------------------------------------
# Module-level convenience function for use from src/swarm/broker.py
# ---------------------------------------------------------------------------
_router: Optional[JurisdictionRouter] = None


def get_router() -> JurisdictionRouter:
    global _router
    if _router is None:
        _router = JurisdictionRouter()
    return _router


def jurisdiction_hint(domain: str = "general", team: str = "default") -> dict:
    """Convenience function: return routing hint for the existing broker."""
    try:
        return get_router().decide_for_existing_broker(domain=domain, team=team)
    except Exception as e:
        logger.warning("jurisdiction_hint failed (returning empty hint): %s", e)
        return {}
