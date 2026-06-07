"""MyDude Model Promotion Gate with exec-locus assertion.

Consumes Foundry continuous-eval + Azure Monitor signals and rotates the
governed model roster in agents_home.policy.model_team_policy by benchmark
+ cost + modality + exec_locus.

EXEC-LOCUS RULE: A model on the wrong infrastructure can never satisfy a
domain's pinned exec_locus. This gate asserts that rule before every promotion.

Eval signal sources:
  - Azure Monitor metric: provider_latency_ms, model_benchmark_score
  - Foundry continuous-eval endpoint (configured via FOUNDRY_EVAL_ENDPOINT)
  - provider_home.candidates.model_candidate (local eval results)

The gate writes only to agents_home.policy.model_team_policy, via the
agents_home_writer role. It does NOT write to Unity Catalog — only the BCS
gate may do that. The promotion event is submitted as a CompletionClaim to
the BCS gate for the catalog record.

Usage:
    python model_promotion_gate.py --domain general --dry-run
    python model_promotion_gate.py --domain all
"""
from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("model_promotion_gate")

# exec_locus → allowed providers mapping (loaded from agents_home at runtime)
EXEC_LOCUS_PROVIDERS: dict[str, set[str]] = {
    "in_azure": {"openai", "gemini", "grok", "azure_openai", "foundry_maas"},
    "anthropic_hosted": {"anthropic"},
    "local": {"ollama", "mlx", "qwen3", "llama_cpp"},
}


@dataclass
class EvalSignal:
    model_id: str
    provider: str
    domain: str
    exec_locus: str
    benchmark_score: float
    cost_per_1k_tokens: float
    latency_p50_ms: int
    latency_p99_ms: int
    modality: list[str]
    source: str
    evaluated_at: str = ""

    def __post_init__(self):
        if not self.evaluated_at:
            self.evaluated_at = datetime.now(timezone.utc).isoformat()


class ExecLocusViolation(ValueError):
    """Raised when a model's provider does not match the domain's pinned exec_locus."""


def _assert_exec_locus(model_id: str, provider: str, domain_exec_locus_pin: str) -> None:
    """Assert that the model's provider can satisfy the domain's pinned exec_locus.

    A model on the wrong infrastructure can NEVER satisfy an exec_locus-pinned
    domain. This is the central invariant of the promotion gate.
    """
    allowed = EXEC_LOCUS_PROVIDERS.get(domain_exec_locus_pin, set())
    if not allowed:
        raise ExecLocusViolation(
            "exec_locus_pin '%s' has no registered provider set — cannot validate." % domain_exec_locus_pin
        )
    if provider not in allowed:
        raise ExecLocusViolation(
            "exec_locus assertion FAILED: model '%s' from provider '%s' cannot satisfy "
            "exec_locus_pin '%s'. Allowed providers for this locus: %s. "
            "A model on the wrong infrastructure can never be promoted to an exec_locus-pinned domain."
            % (model_id, provider, domain_exec_locus_pin, sorted(allowed))
        )


def _fetch_foundry_eval_signals(domain: str) -> list[EvalSignal]:
    """Fetch continuous-eval signals from the Foundry endpoint."""
    endpoint = os.environ.get("FOUNDRY_EVAL_ENDPOINT", "")
    token = os.environ.get("FOUNDRY_EVAL_TOKEN", "")
    if not endpoint or not token:
        logger.info("FOUNDRY_EVAL_ENDPOINT or FOUNDRY_EVAL_TOKEN not set; skipping Foundry signals.")
        return []
    try:
        import urllib.request
        url = "%s/api/eval/signals?domain=%s" % (endpoint.rstrip("/"), domain)
        req = urllib.request.Request(url, headers={"Authorization": "Bearer %s" % token})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        signals = []
        for item in data.get("signals", []):
            signals.append(EvalSignal(
                model_id=item["model_id"],
                provider=item["provider"],
                domain=domain,
                exec_locus=item.get("exec_locus", "in_azure"),
                benchmark_score=float(item.get("benchmark_score", 0)),
                cost_per_1k_tokens=float(item.get("cost_per_1k_tokens", 0)),
                latency_p50_ms=int(item.get("latency_p50_ms", 0)),
                latency_p99_ms=int(item.get("latency_p99_ms", 0)),
                modality=item.get("modality", ["text"]),
                source="foundry_eval",
            ))
        logger.info("Fetched %d eval signals from Foundry for domain=%s", len(signals), domain)
        return signals
    except Exception as e:
        logger.warning("Failed to fetch Foundry eval signals: %s", e)
        return []


def _fetch_local_candidate_signals(domain: str) -> list[EvalSignal]:
    """Fetch eval signals from provider_home.candidates.model_candidate."""
    dsn = os.environ.get("PG_PROVIDER_HOME_DSN", "")
    if not dsn:
        return []
    try:
        import psycopg2
        conn = psycopg2.connect(dsn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT model_id, provider, exec_locus,
                       benchmark_score, cost_per_1k_tokens,
                       latency_p50_ms, latency_p99_ms, modality
                FROM candidates.model_candidate
                WHERE domain = %s AND promotion_status = 'pending'
                ORDER BY benchmark_score DESC NULLS LAST
                LIMIT 20
                """,
                (domain,),
            )
            rows = cur.fetchall()
        conn.close()
        signals = []
        for row in rows:
            model_id, provider, exec_locus, bench, cost, p50, p99, modality = row
            signals.append(EvalSignal(
                model_id=model_id or "",
                provider=provider or "",
                domain=domain,
                exec_locus=exec_locus or "in_azure",
                benchmark_score=float(bench or 0),
                cost_per_1k_tokens=float(cost or 0),
                latency_p50_ms=int(p50 or 0),
                latency_p99_ms=int(p99 or 0),
                modality=list(modality or ["text"]),
                source="local_candidate",
            ))
        return signals
    except Exception as e:
        logger.warning("Failed to fetch local candidate signals: %s", e)
        return []


def _score_candidate(signal: EvalSignal) -> float:
    """Composite score: higher is better. Weighted benchmark + cost + latency."""
    bench = signal.benchmark_score or 0.0
    cost = signal.cost_per_1k_tokens or 0.0
    latency = signal.latency_p50_ms or 0.0
    cost_score = max(0.0, 1.0 - (cost / 0.10)) if cost > 0 else 0.5
    latency_score = max(0.0, 1.0 - (latency / 5000)) if latency > 0 else 0.5
    return 0.6 * bench + 0.25 * cost_score + 0.15 * latency_score


def _get_domain_exec_locus_pin(domain: str) -> str:
    """Look up a domain's exec_locus_pin from agents_home.policy.model_team_policy."""
    dsn = os.environ.get("PG_AGENTS_HOME_DSN", "")
    if not dsn:
        logger.warning("PG_AGENTS_HOME_DSN not set; using default exec_locus_pin 'in_azure' for domain %s", domain)
        return "in_azure"
    try:
        import psycopg2
        conn = psycopg2.connect(dsn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT exec_locus_pin FROM policy.model_team_policy WHERE domain = %s AND allowed = TRUE LIMIT 1",
                (domain,),
            )
            row = cur.fetchone()
        conn.close()
        if row:
            return row[0]
    except Exception as e:
        logger.warning("Could not fetch domain exec_locus_pin: %s", e)
    return "in_azure"


def _promote_model_in_agents_home(signal: EvalSignal, score: float, team: str = "default") -> None:
    """Write the promoted model to agents_home.policy.model_team_policy."""
    dsn = os.environ.get("PG_AGENTS_HOME_DSN", "")
    if not dsn:
        logger.warning("PG_AGENTS_HOME_DSN not set; promotion written to log only.")
        return
    try:
        import psycopg2
        conn = psycopg2.connect(dsn)
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO policy.model_team_policy
                        (team, domain, model_id, provider, exec_locus_pin, allowed, priority, updated_at)
                    VALUES (%s, %s, %s, %s, %s, TRUE, %s, now())
                    ON CONFLICT (team, domain, model_id, provider)
                    DO UPDATE SET
                        exec_locus_pin = EXCLUDED.exec_locus_pin,
                        allowed = TRUE,
                        priority = EXCLUDED.priority,
                        updated_at = now()
                    """,
                    (team, signal.domain, signal.model_id, signal.provider,
                     signal.exec_locus, int(score * 1000)),
                )
        conn.close()
        logger.info(
            "Promoted model to agents_home: %s/%s domain=%s exec_locus=%s score=%.4f",
            signal.provider, signal.model_id, signal.domain, signal.exec_locus, score,
        )
    except Exception as e:
        logger.error("Failed to write promotion to agents_home: %s", e)


def promote_domain(domain: str, dry_run: bool = False, team: str = "default") -> list[dict]:
    """Run the promotion gate for a single domain. Returns list of promotion records."""
    logger.info("Running model promotion gate for domain=%s dry_run=%s", domain, dry_run)

    # Gather eval signals
    signals = _fetch_foundry_eval_signals(domain) + _fetch_local_candidate_signals(domain)
    if not signals:
        logger.warning("No eval signals available for domain=%s; nothing to promote.", domain)
        return []

    # Get domain's exec_locus_pin
    exec_locus_pin = _get_domain_exec_locus_pin(domain)
    logger.info("Domain '%s' has exec_locus_pin='%s'", domain, exec_locus_pin)

    promotions = []
    violations = []

    for signal in signals:
        # EXEC-LOCUS ASSERTION — the central invariant
        try:
            _assert_exec_locus(signal.model_id, signal.provider, exec_locus_pin)
        except ExecLocusViolation as e:
            logger.warning("exec_locus violation (skipping): %s", e)
            violations.append({"model_id": signal.model_id, "provider": signal.provider, "reason": str(e)})
            continue

        score = _score_candidate(signal)
        logger.info(
            "Candidate %s/%s score=%.4f exec_locus=%s ✓",
            signal.provider, signal.model_id, score, signal.exec_locus,
        )

        if not dry_run:
            # GATE-FIRST: BCS claim MUST succeed before any roster write.
            # If BCS gate rejects or is unreachable, the model is NOT promoted.
            from pathlib import Path
            import hashlib
            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent / "migrators"))
            from base import CompletionClaim, MigrationAuthority, ScopeGate, ScopeLabel, submit_completion_claim

            claim = CompletionClaim(
                exec_locus=signal.exec_locus,
                authority=MigrationAuthority.POSTGRES,
                scope_label=ScopeLabel.V7_SCOPE_LABEL,
                migration_name="model_promotion",
                database="agents_home",
                detail={
                    "model_id": signal.model_id,
                    "provider": signal.provider,
                    "domain": domain,
                    "score": score,
                    "exec_locus_pin": exec_locus_pin,
                    "source": signal.source,
                },
            )
            claim.content_hash = hashlib.sha256(
                ("%s::%s::%s::%f" % (signal.model_id, signal.provider, domain, score)).encode()
            ).hexdigest()
            gate = ScopeGate(claim, expected_authority=MigrationAuthority.POSTGRES)
            bcs_confirmed = False
            try:
                gate.run_all()
                bcs_result = submit_completion_claim(claim)
                # "queued_for_replay", "logged_only", and "error" are NOT confirmed commits.
                # They mean BCS was unreachable and the claim went to the local outbox.
                # The roster MUST NOT advance until BCS commits the claim in Unity.
                confirmed_statuses = {"ok", "promoted", "accepted", "unity_committed"}
                result_status = (bcs_result or {}).get("status", "")
                if result_status not in confirmed_statuses:
                    raise RuntimeError(
                        "BCS gate did not confirm Unity commit (status=%r). "
                        "Claim is queued or in error — roster update withheld until replay." % result_status
                    )
                bcs_confirmed = True
            except Exception as e:
                # BCS gate rejected or unreachable — roster MUST NOT be updated.
                logger.error(
                    "BCS claim submission NOT confirmed — model %s/%s domain=%s NOT promoted. "
                    "Roster update aborted (candidate remains in outbox for replay). Error: %s",
                    signal.provider, signal.model_id, domain, e,
                )
                violations.append({
                    "model_id": signal.model_id,
                    "provider": signal.provider,
                    "reason": "bcs_not_confirmed: %s" % e,
                })
                continue  # skip _promote_model_in_agents_home entirely

            # BCS gate confirmed Unity commit — roster update is now safe
            assert bcs_confirmed, "Invariant: roster write must not occur without BCS confirmation"
            _promote_model_in_agents_home(signal, score, team=team)

        promotions.append({
            "model_id": signal.model_id,
            "provider": signal.provider,
            "domain": domain,
            "exec_locus": signal.exec_locus,
            "exec_locus_pin": exec_locus_pin,
            "score": score,
            "dry_run": dry_run,
        })

    logger.info(
        "Promotion gate complete for domain=%s: %d promoted, %d exec_locus violations",
        domain, len(promotions), len(violations),
    )
    return promotions


def main():
    import argparse
    parser = argparse.ArgumentParser(description="MyDude Model Promotion Gate")
    parser.add_argument("--domain", default="general", help="Domain to promote (or 'all')")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--team", default="default")
    args = parser.parse_args()

    domains = ["general", "local"] if args.domain == "all" else [args.domain]
    for domain in domains:
        results = promote_domain(domain, dry_run=args.dry_run, team=args.team)
        for r in results:
            print(json.dumps(r))


if __name__ == "__main__":
    main()
