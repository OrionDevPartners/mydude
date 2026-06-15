"""
Capability acquisition orchestrator — the closed acquisition loop.

Entry point: trigger_acquisition(capability, params)

Flow:
  1. Open a CapabilityAcquisitionJob row in state=pending (kill-switch gated).
  2. Parallel fetch: registry search (PyPI, npm) + web knowledge harvest.
  3. Per-candidate sandboxed verification (no production secrets).
  4. Governance envelope check (compliance/HR thresholds via swarm).
  5. Raise a GovernanceProposal (safety track) for operator approval.
  6. On enactment: register the capability, siphon to memory, audit success.
  7. All outcomes audited secret-free; failures do not propagate to callers.

Kill switch: ENABLE_AUTO_SIPHON_ACQUISITION (env var, default=false).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ACQUISITION_KILL_SWITCH_ENV = "ENABLE_AUTO_SIPHON_ACQUISITION"

# How many days a previously-rejected candidate stays deduplicated (skipped on
# re-acquisition). Configurable via env; falls back to a sane default.
_ACQUISITION_DEDUP_DAYS_ENV = "ACQUISITION_DEDUP_DAYS"
_DEFAULT_DEDUP_DAYS = 7

MIN_COMPLIANCE = 0.80
MAX_HALLUCINATION_RISK = 0.25


def _dedup_days() -> int:
    """Window (in days) for skipping candidates that already failed governance.

    Reads ACQUISITION_DEDUP_DAYS; defaults to 7. Non-positive / unparseable
    values fall back to the default.
    """
    raw = os.environ.get(_ACQUISITION_DEDUP_DAYS_ENV, "").strip()
    if raw:
        try:
            val = int(raw)
            if val > 0:
                return val
        except ValueError:
            pass
    return _DEFAULT_DEDUP_DAYS


def _candidate_history(capability: str, days: int) -> Dict[str, str]:
    """Recent per-candidate outcomes for a capability within the last `days`.

    Returns a map of normalized candidate name -> "approved" | "rejected".
    A candidate that ever passed governance in the window is "approved" (and
    that verdict wins over any rejection); otherwise "rejected". Joins
    AcquisitionCandidate to its parent job to scope by capability.

    Never raises — on any failure returns {} so acquisition proceeds normally.
    """
    history: Dict[str, str] = {}
    try:
        from src.database import SessionLocal
        from src.models import AcquisitionCandidate, CapabilityAcquisitionJob
        cutoff = datetime.utcnow() - timedelta(days=days)
        db = SessionLocal()
        try:
            rows = (
                db.query(AcquisitionCandidate)
                .join(
                    CapabilityAcquisitionJob,
                    AcquisitionCandidate.job_id == CapabilityAcquisitionJob.id,
                )
                .filter(
                    CapabilityAcquisitionJob.capability == capability,
                    AcquisitionCandidate.created_at >= cutoff,
                )
                .all()
            )
            for row in rows:
                name = (row.candidate_name or "").strip().lower()
                if not name:
                    continue
                if row.passed_governance:
                    history[name] = "approved"
                elif history.get(name) != "approved":
                    history[name] = "rejected"
        finally:
            db.close()
    except Exception as exc:
        logger.debug(
            "acquisition: candidate history lookup failed (non-blocking): %s", exc
        )
    return history


def _kill_switch_enabled() -> bool:
    raw = os.environ.get(_ACQUISITION_KILL_SWITCH_ENV, "").lower().strip()
    return raw in ("1", "true", "yes", "on")


def _audit(capability: str, status: str, detail: str = "", target: str = "") -> None:
    """Fire-and-forget capability audit — never raises."""
    try:
        from src.swarm.integrations import audit_capability
        audit_capability(
            "capability_acquisition",
            target=(target or capability)[:2000],
            status=status,
            detail=detail[:2000],
            source="auto_siphon",
        )
    except Exception as exc:
        logger.warning("acquisition audit failed: %s", exc)


def _open_job(capability: str, params_json: str) -> Optional[int]:
    """Create a CapabilityAcquisitionJob row in state=pending. Returns job id."""
    try:
        from src.database import SessionLocal
        from src.models import CapabilityAcquisitionJob
        db = SessionLocal()
        try:
            job = CapabilityAcquisitionJob(
                job_id=f"ACQ-{uuid.uuid4().hex[:10].upper()}",
                capability=capability,
                params_json=params_json[:4000],
                state="pending",
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            return job.id
        finally:
            db.close()
    except Exception as exc:
        logger.warning("acquisition: could not open job for %s: %s", capability, exc)
        return None


def _update_job(job_id: int, **kwargs) -> None:
    """Update job fields. Never raises."""
    try:
        from src.database import SessionLocal
        from src.models import CapabilityAcquisitionJob
        db = SessionLocal()
        try:
            job = db.get(CapabilityAcquisitionJob, job_id)
            if job is None:
                return
            for k, v in kwargs.items():
                setattr(job, k, v)
            db.commit()
        finally:
            db.close()
    except Exception as exc:
        logger.warning("acquisition: _update_job(%s) failed: %s", job_id, exc)


def _save_candidate(
    job_id: int,
    candidate_name: str,
    candidate_version: str,
    registry: str,
    description: str,
    knowledge_excerpt: str,
    sandbox_result_json: str,
    passed_sandbox: bool,
    passed_governance: bool,
    governance_proposal_id: Optional[str],
) -> Optional[int]:
    """Persist an AcquisitionCandidate row. Returns row id."""
    try:
        from src.database import SessionLocal
        from src.models import AcquisitionCandidate
        db = SessionLocal()
        try:
            row = AcquisitionCandidate(
                job_id=job_id,
                candidate_name=candidate_name[:120],
                candidate_version=candidate_version[:40],
                registry=registry[:40],
                description=description[:500],
                knowledge_excerpt=knowledge_excerpt[:3000],
                sandbox_result_json=sandbox_result_json[:4000],
                passed_sandbox=passed_sandbox,
                passed_governance=passed_governance,
                governance_proposal_id=governance_proposal_id,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return row.id
        finally:
            db.close()
    except Exception as exc:
        logger.warning("acquisition: _save_candidate failed: %s", exc)
        return None


def _governance_score(candidate_name: str, description: str, knowledge: str) -> Dict[str, float]:
    """Run a compact governance check on the candidate via the swarm.

    Uses a short, governed LLM call to assess compliance and hallucination risk
    of integrating this package. Falls back to thresholds that fail-closed
    (compliance=0.0, hr=1.0) when no provider is available — never bypasses.
    """
    try:
        from src.swarm.llm_multi import MultiProviderLLM
        from src.swarm.compliance import ComplianceScorer
        from src.swarm.hallucination import HallucinationRiskModel

        prompt = (
            f"Evaluate the safety and compliance of adding this Python package to an "
            f"AI automation platform:\n\nPackage: {candidate_name}\n"
            f"Description: {description[:300]}\n"
            f"Knowledge: {knowledge[:500]}\n\n"
            f"Respond with a short assessment of (a) whether it introduces safety risks, "
            f"(b) whether the description is verifiable, "
            f"(c) any compliance concerns. "
            f"Keep it factual and concise."
        )
        llm = MultiProviderLLM()
        response = llm.complete(prompt, max_tokens=300)
        cs = ComplianceScorer().score(response)
        hr = HallucinationRiskModel().assess(response)
        return {"compliance": cs, "hallucination_risk": hr, "response": response[:500]}
    except Exception as exc:
        logger.warning("acquisition: governance_score failed: %s", exc)
        return {"compliance": 0.0, "hallucination_risk": 1.0, "response": "governance unavailable"}


def _raise_governance_proposal(
    capability: str,
    job_id: int,
    candidate_name: str,
    candidate_version: str,
    registry: str,
    description: str,
    governance_scores: Dict[str, Any],
) -> Optional[str]:
    """Raise a safety-track GovernanceProposal for operator approval."""
    try:
        from src.swarm.governance_engine import GovernanceEngine
        ge = GovernanceEngine()
        compliance = governance_scores.get("compliance", 0.0)
        hr = governance_scores.get("hallucination_risk", 1.0)
        title = (
            f"[Auto-Siphon] Approve capability '{capability}' via {candidate_name}=={candidate_version}"
        )
        description_text = (
            f"Acquisition job #{job_id} found a candidate package that passed sandbox "
            f"verification.\n\n"
            f"Package: {candidate_name} {candidate_version} ({registry})\n"
            f"Description: {description[:300]}\n"
            f"Governance scores: compliance={compliance:.3f}, hallucination_risk={hr:.3f}\n\n"
            f"Sandbox verdict: PASSED\n\n"
            f"Approve this proposal to register the capability. Reject to discard."
        )
        proposed_action = (
            f"register_acquired_capability capability={capability} "
            f"package={candidate_name} version={candidate_version} "
            f"registry={registry} job_id={job_id}"
        )
        evidence = [
            f"job_id:{job_id}",
            f"package:{candidate_name}=={candidate_version}",
            f"registry:{registry}",
            f"compliance:{compliance:.3f}",
            f"hallucination_risk:{hr:.3f}",
        ]
        proposal = ge.raise_proposal(
            origin="auto_siphon",
            track="safety",
            title=title[:200],
            description=description_text,
            proposed_action=proposed_action,
            evidence=evidence,
        )
        if proposal:
            return getattr(proposal, "proposal_id", None)
    except Exception as exc:
        logger.warning("acquisition: raise_governance_proposal failed: %s", exc)
    return None


def _siphon_success(
    capability: str,
    candidate_name: str,
    candidate_version: str,
    registry: str,
    knowledge_excerpt: str,
) -> None:
    """Distill the successful acquisition into long-term memory via the existing siphon."""
    try:
        from src.memory.substrate import get_substrate
        substrate = get_substrate()
        content = (
            f"Capability '{capability}' acquired via package {candidate_name}=={candidate_version} "
            f"({registry}). Passed sandbox verification and governance approval. "
            f"Integration: {knowledge_excerpt[:300]}"
        )
        substrate.write_claim(
            content=content[:1000],
            category="capability_acquisition",
            confidence=0.85,
            source=f"auto_siphon:{capability}",
            verified=True,
            metadata={
                "capability": capability,
                "package": candidate_name,
                "version": candidate_version,
                "registry": registry,
                "kind": "acquired_capability",
            },
        )
    except Exception as exc:
        logger.warning("acquisition: siphon_success failed: %s", exc)


def _run_acquisition(capability: str, params: Dict[str, Any], job_id: int) -> None:
    """Run the full acquisition loop for one capability deficit. Never raises."""
    try:
        from .interface import PackageCandidate
        from .pypi_adapter import PyPIAdapter
        from .npm_adapter import NpmAdapter
        from .web_knowledge_adapter import WebKnowledgeAdapter
        from .sandbox import verify_candidate

        descriptor = (
            f"{capability.replace('_', ' ')} "
            f"{params.get('description', '')} "
            f"{params.get('intent', '')}"
        ).strip()[:200]

        _update_job(job_id, state="fetching")
        _audit(capability, "info", f"acquisition job #{job_id} started: fetching candidates")

        registry_adapters = [PyPIAdapter(), NpmAdapter()]
        knowledge_adapter = WebKnowledgeAdapter()

        all_candidates: List[PackageCandidate] = []

        with ThreadPoolExecutor(max_workers=len(registry_adapters)) as ex:
            futures = {
                ex.submit(adapter.search, descriptor, max_results=3): adapter
                for adapter in registry_adapters
            }
            for fut in as_completed(futures, timeout=30):
                try:
                    result = fut.result()
                    all_candidates.extend(result.candidates)
                    if result.error:
                        logger.debug("acquisition: registry error: %s", result.error)
                except Exception as exc:
                    logger.debug("acquisition: registry future error: %s", exc)

        if not all_candidates:
            _update_job(job_id, state="failed",
                        notes="No candidates found from any registry.")
            _audit(capability, "error", "no candidates found", target=capability)
            return

        all_candidates.sort(key=lambda c: c.score, reverse=True)

        # Learn from past rejections: skip candidates that already failed
        # governance review recently, and warn (instead of re-running) on
        # candidates that were already approved — the capability may already be
        # registered. This avoids redundant network fetches, wasted sandbox
        # time, and duplicate governance proposals for the same package.
        dedup_days = _dedup_days()
        history = _candidate_history(capability, dedup_days)
        fresh_candidates: List[PackageCandidate] = []
        skipped_rejected: List[str] = []
        warned_approved: List[str] = []
        for pkg in all_candidates:
            status = history.get((pkg.name or "").strip().lower())
            if status == "rejected":
                skipped_rejected.append(f"{pkg.name}=={pkg.version}")
            elif status == "approved":
                warned_approved.append(f"{pkg.name}=={pkg.version}")
            else:
                fresh_candidates.append(pkg)

        if skipped_rejected:
            msg = (
                f"dedup: skipping {len(skipped_rejected)} candidate(s) that failed "
                f"governance within {dedup_days}d: {', '.join(skipped_rejected[:5])}"
            )
            logger.info("acquisition %s: %s", capability, msg)
            _audit(capability, "info", msg, target=capability)
        if warned_approved:
            msg = (
                f"dedup warning: {len(warned_approved)} candidate(s) already approved "
                f"within {dedup_days}d (capability may already be registered): "
                f"{', '.join(warned_approved[:5])}"
            )
            logger.warning("acquisition %s: %s", capability, msg)
            _audit(capability, "warning", msg, target=capability)

        candidates_to_verify = fresh_candidates[:3]

        if not candidates_to_verify:
            if warned_approved:
                note = (
                    f"Skipped: all candidates already approved within {dedup_days}d "
                    f"(capability may already be registered): "
                    f"{', '.join(warned_approved[:3])}"
                )
            elif skipped_rejected:
                note = (
                    f"Skipped: all candidates failed governance within {dedup_days}d; "
                    f"no re-acquisition: {', '.join(skipped_rejected[:3])}"
                )
            else:
                note = "Skipped: all candidates deduplicated against recent history."
            _update_job(job_id, state="rejected", notes=note[:1000])
            _audit(capability, "rejected", note, target=capability)
            return

        _update_job(job_id, state="sandboxing")

        # Harvest knowledge for all candidates IN PARALLEL with each other.
        # Knowledge harvesting (web search/docs scraping) is I/O-bound and
        # independent across candidates, so we fan it out concurrently before
        # entering the sequential sandbox verification loop.
        knowledge_map: Dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=len(candidates_to_verify) or 1,
                                thread_name_prefix="acq-harvest") as harvest_ex:
            harvest_futures = {
                harvest_ex.submit(knowledge_adapter.harvest, pkg): pkg
                for pkg in candidates_to_verify
            }
            for fut in as_completed(harvest_futures, timeout=30):
                pkg = harvest_futures[fut]
                try:
                    result_obj = fut.result()
                    knowledge_map[pkg.name] = result_obj.excerpt if result_obj else ""
                except Exception as harvest_exc:
                    logger.debug("knowledge harvest failed for %s: %s", pkg.name, harvest_exc)
                    knowledge_map[pkg.name] = ""

        passed_candidates = []
        sandbox_results: Dict[str, bool] = {}
        for pkg in candidates_to_verify:
            knowledge_text = knowledge_map.get(pkg.name, "")

            sandbox_res = verify_candidate(pkg, descriptor)
            sandbox_json = json.dumps(sandbox_res.to_dict())
            sandbox_results[pkg.name] = sandbox_res.passed

            gov_scores: Dict[str, Any] = {"compliance": 0.0, "hallucination_risk": 1.0}
            if sandbox_res.passed:
                gov_scores = _governance_score(pkg.name, pkg.description, knowledge_text)

            compliance = gov_scores.get("compliance", 0.0)
            hr = gov_scores.get("hallucination_risk", 1.0)
            passed_gov = (
                sandbox_res.passed
                and compliance >= MIN_COMPLIANCE
                and hr <= MAX_HALLUCINATION_RISK
            )

            proposal_id = None
            if passed_gov:
                proposal_id = _raise_governance_proposal(
                    capability, job_id,
                    pkg.name, pkg.version, pkg.registry,
                    pkg.description, gov_scores,
                )

            _save_candidate(
                job_id=job_id,
                candidate_name=pkg.name,
                candidate_version=pkg.version,
                registry=pkg.registry,
                description=pkg.description,
                knowledge_excerpt=knowledge_text,
                sandbox_result_json=sandbox_json,
                passed_sandbox=sandbox_res.passed,
                passed_governance=passed_gov,
                governance_proposal_id=proposal_id,
            )

            if passed_gov:
                passed_candidates.append((pkg, knowledge_text, proposal_id))

        if passed_candidates:
            pkg, knowledge_text, proposal_id = passed_candidates[0]
            _update_job(
                job_id,
                state="governance_pending",
                notes=(
                    f"Best candidate: {pkg.name}=={pkg.version} ({pkg.registry}). "
                    f"Governance proposal: {proposal_id or 'raised'}. "
                    f"Awaiting operator approval."
                ),
                best_candidate_name=pkg.name,
                best_candidate_version=pkg.version,
                best_candidate_registry=pkg.registry,
                governance_proposal_id=proposal_id,
            )
            _audit(
                capability, "governance_pending",
                f"candidate {pkg.name}=={pkg.version} awaiting governance approval "
                f"(proposal={proposal_id})",
                target=pkg.name,
            )
        else:
            reason_parts = []
            for pkg in candidates_to_verify:
                sandbox_ok = sandbox_results.get(pkg.name, False)
                reason_parts.append(
                    f"{pkg.name}=={pkg.version}: sandbox={'ok' if sandbox_ok else 'fail'}"
                )
            _update_job(
                job_id,
                state="rejected",
                notes="No candidate passed sandbox + governance. " + "; ".join(reason_parts[:3]),
            )
            _audit(
                capability, "rejected",
                "no candidate passed sandbox+governance thresholds",
                target=capability,
            )

    except Exception as exc:
        logger.error("acquisition: _run_acquisition(%s) crashed: %s", capability, exc)
        _update_job(job_id, state="failed", notes=f"Internal error: {exc!s:.300}")
        _audit(capability, "error", f"acquisition crashed: {exc!s:.300}", target=capability)


def _dedup_check(capability: str) -> bool:
    """Return True if there is already an active (non-terminal) or approved
    acquisition job for this capability.

    Prevents flooding: the broker may see many requests for the same unmet
    capability before the first job completes. We skip opening a duplicate.
    Terminal states (rejected/failed) are NOT deduplicated — a retry is fine.
    """
    try:
        from src.database import SessionLocal
        from src.models import CapabilityAcquisitionJob
        db = SessionLocal()
        try:
            existing = (
                db.query(CapabilityAcquisitionJob)
                .filter(
                    CapabilityAcquisitionJob.capability == capability,
                    CapabilityAcquisitionJob.state.in_(
                        ["pending", "fetching", "sandboxing", "governance_pending", "approved"]
                    ),
                )
                .first()
            )
            if existing:
                logger.debug(
                    "acquisition dedup: skipping %s (existing job %s state=%s)",
                    capability, existing.job_id, existing.state,
                )
                return True
        finally:
            db.close()
    except Exception as exc:
        logger.debug("acquisition dedup check failed (non-blocking): %s", exc)
    return False


def trigger_acquisition(capability: str, params: Optional[Dict[str, Any]] = None) -> None:
    """Trigger an async capability acquisition job for an unmet capability deficit.

    Called fire-and-forget from the broker's unimplemented-capability path.
    Does nothing (no-op, no error) when the kill switch is off.
    Deduplicates: skips if an active or approved job already exists for this
    capability — the broker may fire multiple times before any job completes.
    Never raises — failures are logged and audited internally.
    """
    if not _kill_switch_enabled():
        return

    if _dedup_check(capability):
        return

    params = params or {}
    try:
        params_json = json.dumps({
            k: v for k, v in params.items()
            if isinstance(v, (str, int, float, bool, type(None)))
        })
    except Exception:
        params_json = "{}"

    job_id = _open_job(capability, params_json)
    if job_id is None:
        _audit(capability, "error", "could not open acquisition job")
        return

    try:
        from src.swarm.error_metrics import record_sentinel_event
        record_sentinel_event(
            alert_type="capability_acquisition_started",
            severity="info",
            description=(
                f"Acquisition job #{job_id} opened for capability '{capability}'. "
                f"Fetching candidates from PyPI/npm and running sandboxed verification."
            ),
            recommended_action="Monitor the Governance Center for a pending approval proposal.",
        )
    except Exception:
        pass

    t = threading.Thread(
        target=_run_acquisition,
        args=(capability, params, job_id),
        daemon=True,
        name=f"acquisition-{capability[:30]}",
    )
    t.start()
