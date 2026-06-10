"""Persistence + governance-safe state transitions for prompt versions.

dspy-free (safe to import at app startup for seeding + orphan recovery). Live
instructions are read per-call from the DB — the governed judge runs at most once
per swarm run, so a single indexed query is cheaper than any cache-invalidation
race across promotion/rollback/quorum paths.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.database import SessionLocal
from src.models import (
    GovernanceEnactment,
    PromptOptimizationRun,
    PromptProgram,
    PromptTrace,
    PromptVersion,
)
from src.promptopt.specs import PROGRAM_SPECS, get_spec

_seed_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _version_by_id(db, version_id: int) -> Optional[PromptVersion]:
    return db.query(PromptVersion).filter_by(id=version_id).first()


def _program_by_id(db, program_id: int) -> Optional[PromptProgram]:
    return db.query(PromptProgram).filter_by(id=program_id).first()


def get_program(db, name: str) -> Optional[PromptProgram]:
    return db.query(PromptProgram).filter_by(name=name).first()


# ---------------------------------------------------------------------------
# Seeding (idempotent)
# ---------------------------------------------------------------------------

def seed_default_programs() -> None:
    """Create each program + its version-1 live snapshot if missing. Idempotent."""
    with _seed_lock:
        db = SessionLocal()
        try:
            for name, spec in PROGRAM_SPECS.items():
                prog = get_program(db, name)
                if prog is None:
                    prog = PromptProgram(
                        name=name,
                        signature_name=spec.signature_name,
                        description=spec.description,
                    )
                    db.add(prog)
                    db.flush()
                if prog.current_version_id is None:
                    v = PromptVersion(
                        program_id=prog.id,
                        version_no=1,
                        instructions=spec.seed_instructions,
                        demos_json="[]",
                        provenance_json=json.dumps(
                            {"optimizer": "seed", "source": "code-hardcoded",
                             "date": _now_iso()}
                        ),
                        status="live",
                        ever_live=True,
                        score=None,
                        promoted_at=datetime.utcnow(),
                    )
                    db.add(v)
                    db.flush()
                    prog.current_version_id = v.id
                elif prog.current_version_id is not None:
                    # Idempotent backfill: a version seeded/promoted before the
                    # promoted_at column existed is genuinely null. Being live means
                    # it went live at creation, so anchor it to created_at instead
                    # of leaving a permanently-empty audit field.
                    cur = _version_by_id(db, prog.current_version_id)
                    if cur is not None and cur.promoted_at is None:
                        cur.promoted_at = cur.created_at or datetime.utcnow()
                db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Live read (per-call, fail-loud)
# ---------------------------------------------------------------------------

def get_live_instructions(name: str) -> Tuple[int, str, List[Dict[str, Any]]]:
    """Return (version_id, instructions, demos) for the live version of a program.

    Seeds lazily if the program has never been initialized. Fails loud if, after
    seeding, there is still no live version (a real invariant violation).
    """
    db = SessionLocal()
    try:
        prog = get_program(db, name)
        if prog is None or prog.current_version_id is None:
            db.close()
            seed_default_programs()
            db = SessionLocal()
            prog = get_program(db, name)
        if prog is None or prog.current_version_id is None:
            raise RuntimeError("No live prompt version for program '%s'" % name)
        v = _version_by_id(db, prog.current_version_id)
        if v is None:
            raise RuntimeError(
                "Program '%s' points at missing version %s" % (name, prog.current_version_id)
            )
        demos = json.loads(v.demos_json or "[]")
        return v.id, v.instructions, demos
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Trace capture
# ---------------------------------------------------------------------------

_TRACE_INPUT_CAP = 12000
_TRACE_OUTPUT_CAP = 16000


def record_trace(
    program_name: str,
    version_id: Optional[int],
    inputs: Dict[str, Any],
    output: str,
    score_info: Optional[Dict[str, Any]] = None,
    status: str = "ok",
    feedback: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """Persist a single governed run trace. Best-effort: never raises into the
    hot path (a trace write failure must not break a swarm run)."""
    db = SessionLocal()
    try:
        prog = get_program(db, program_name)
        if prog is None:
            return None
        capped_inputs = {
            k: (v[:_TRACE_INPUT_CAP] if isinstance(v, str) else v)
            for k, v in (inputs or {}).items()
        }
        t = PromptTrace(
            program_id=prog.id,
            version_id=version_id,
            inputs_json=json.dumps(capped_inputs)[:20000],
            output=(output or "")[:_TRACE_OUTPUT_CAP],
            score=(score_info or {}).get("score"),
            compliance_score=(score_info or {}).get("compliance_score"),
            hallucination_risk=(score_info or {}).get("hallucination_risk"),
            feedback_json=json.dumps(feedback) if feedback else None,
            status=status,
        )
        db.add(t)
        db.commit()
        return t.id
    except Exception:
        db.rollback()
        return None
    finally:
        db.close()


def count_usable_traces(db, program_id: int) -> int:
    return (
        db.query(PromptTrace)
        .filter(PromptTrace.program_id == program_id)
        .filter(PromptTrace.status == "ok")
        .filter(PromptTrace.score.isnot(None))
        .count()
    )


def load_usable_traces(db, program_id: int, limit: int = 200) -> List[PromptTrace]:
    return (
        db.query(PromptTrace)
        .filter(PromptTrace.program_id == program_id)
        .filter(PromptTrace.status == "ok")
        .filter(PromptTrace.score.isnot(None))
        .order_by(PromptTrace.created_at.desc())
        .limit(limit)
        .all()
    )


# ---------------------------------------------------------------------------
# Candidate version creation (by the optimizer)
# ---------------------------------------------------------------------------

def _next_version_no(db, program_id: int) -> int:
    last = (
        db.query(PromptVersion)
        .filter_by(program_id=program_id)
        .order_by(PromptVersion.version_no.desc())
        .first()
    )
    return (last.version_no + 1) if last else 1


def create_candidate_version(
    program_id: int,
    instructions: str,
    demos: List[Dict[str, Any]],
    provenance: Dict[str, Any],
    score: Optional[float],
    optimization_run_id: Optional[int],
) -> int:
    db = SessionLocal()
    try:
        v = PromptVersion(
            program_id=program_id,
            version_no=_next_version_no(db, program_id),
            instructions=instructions,
            demos_json=json.dumps(demos or []),
            provenance_json=json.dumps(provenance or {}),
            status="candidate",
            ever_live=False,
            score=score,
            optimization_run_id=optimization_run_id,
        )
        db.add(v)
        db.commit()
        return v.id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Governance promotion (called INSIDE the enactment's db session)
# ---------------------------------------------------------------------------

def promote_version_in_session(db, version_id: int, proposal_id: int) -> Tuple[bool, str]:
    """Flip a candidate version to live within the caller's transaction.

    The caller (governance enactment) owns the commit. Validates that the target
    is a candidate, archives the prior live version, and repoints the program.
    """
    v = _version_by_id(db, version_id)
    if v is None:
        return False, "version %s not found" % version_id
    if v.status != "candidate":
        return False, "version %s is not a candidate (status=%s)" % (version_id, v.status)
    prog = _program_by_id(db, v.program_id)
    if prog is None:
        return False, "program %s not found" % v.program_id

    if prog.current_version_id and prog.current_version_id != v.id:
        cur = _version_by_id(db, prog.current_version_id)
        if cur is not None and cur.status == "live":
            cur.status = "archived"

    v.status = "live"
    v.ever_live = True
    v.governance_proposal_id = proposal_id
    v.promoted_at = datetime.utcnow()
    prog.current_version_id = v.id
    prog.updated_at = datetime.utcnow()
    return True, "%s=v%s" % (prog.name, v.version_no)


def proposed_action_for(version_id: int) -> str:
    return "promote_prompt_version:%s" % version_id


def propose_promotion(version_id: int, operator: str = "operator") -> Dict[str, Any]:
    """Raise a governance proposal to promote a candidate version to live.

    Promotion does NOT happen here — it only takes effect when the proposal is
    enacted (operator action or quorum) via the existing governance gate. Returns
    the proposal identifiers so the caller can surface/track it.
    """
    db = SessionLocal()
    try:
        v = _version_by_id(db, version_id)
        if v is None:
            raise ValueError("version %s not found" % version_id)
        if v.status != "candidate":
            raise ValueError(
                "only candidate versions can be proposed for promotion "
                "(version %s status=%s)" % (version_id, v.status)
            )
        prog = _program_by_id(db, v.program_id)
        if prog is None:
            raise ValueError("program %s not found" % v.program_id)
        prov = json.loads(v.provenance_json or "{}")
        program_name = prog.name
        version_no = v.version_no
        score = v.score
    finally:
        db.close()

    from src.swarm.governance_engine import GovernanceEngine
    engine = GovernanceEngine()
    title = "Promote %s prompt to v%s" % (program_name, version_no)
    desc = (
        "Promote evolved prompt version %s (v%s) of program '%s' to LIVE. "
        "optimizer=%s score=%s base=%s delta=%s." % (
            version_id, version_no, program_name, prov.get("optimizer"),
            score, prov.get("base_score"), prov.get("delta"),
        )
    )
    proposal_id = engine.raise_proposal(
        origin="operator",
        track="policy",
        title=title,
        description=desc,
        proposed_action=proposed_action_for(version_id),
        evidence=[json.dumps(prov)[:480]],
        source_claim_id=None,
    )
    if not proposal_id:
        raise RuntimeError("failed to raise governance proposal for promotion")

    db = SessionLocal()
    try:
        from src.models import GovernanceProposal
        p = db.query(GovernanceProposal).filter_by(proposal_id=proposal_id).first()
        return {
            "proposal_id": proposal_id,
            "proposal_db_id": p.id if p else None,
            "version_id": version_id,
            "program": program_name,
            "track": "policy",
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Rollback (direct, audited; only to previously-live versions)
# ---------------------------------------------------------------------------

def rollback_to(version_id: int, operator: str = "operator") -> Dict[str, Any]:
    """Revert a program's live version to a previously-live version. Audited via a
    GovernanceEnactment row. Refuses targets that were never live."""
    db = SessionLocal()
    try:
        v = _version_by_id(db, version_id)
        if v is None:
            raise ValueError("version %s not found" % version_id)
        if not v.ever_live:
            raise ValueError(
                "version %s was never live; rollback is restricted to "
                "previously-live versions" % version_id
            )
        prog = _program_by_id(db, v.program_id)
        if prog is None:
            raise ValueError("program %s not found" % v.program_id)
        if prog.current_version_id == v.id:
            raise ValueError("version %s is already live" % version_id)

        prev_live_id = prog.current_version_id
        if prev_live_id:
            cur = _version_by_id(db, prev_live_id)
            if cur is not None and cur.status == "live":
                cur.status = "archived"

        v.status = "live"
        v.ever_live = True
        v.promoted_at = datetime.utcnow()
        prog.current_version_id = v.id
        prog.updated_at = datetime.utcnow()

        db.add(GovernanceEnactment(
            proposal_id=(v.governance_proposal_id or 0),
            enacted_by=(operator or "operator")[:100],
            change_json=json.dumps({
                "action": "rollback_prompt_version",
                "program": prog.name,
                "to_version_id": v.id,
                "to_version_no": v.version_no,
                "from_version_id": prev_live_id,
                "date": _now_iso(),
            }),
        ))
        db.commit()
        return {
            "program": prog.name,
            "version_id": v.id,
            "version_no": v.version_no,
            "from_version_id": prev_live_id,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Read helpers for the API
# ---------------------------------------------------------------------------

def list_programs() -> List[Dict[str, Any]]:
    db = SessionLocal()
    try:
        out = []
        for p in db.query(PromptProgram).order_by(PromptProgram.name).all():
            live = _version_by_id(db, p.current_version_id) if p.current_version_id else None
            out.append({
                "id": p.id,
                "name": p.name,
                "signature_name": p.signature_name,
                "description": p.description,
                "current_version_id": p.current_version_id,
                "live_version_no": live.version_no if live else None,
                "version_count": db.query(PromptVersion).filter_by(program_id=p.id).count(),
                "usable_trace_count": count_usable_traces(db, p.id),
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            })
        return out
    finally:
        db.close()


def _version_row(v: PromptVersion) -> Dict[str, Any]:
    prov = json.loads(v.provenance_json or "{}")
    return {
        "id": v.id,
        "program_id": v.program_id,
        "version_no": v.version_no,
        "status": v.status,
        "ever_live": bool(v.ever_live),
        "optimizer": prov.get("optimizer"),
        "score": v.score,
        "base_score": prov.get("base_score"),
        "delta": prov.get("delta"),
        "instructions": v.instructions,
        "demos": json.loads(v.demos_json or "[]"),
        "provenance": prov,
        "governance_proposal_id": v.governance_proposal_id,
        "optimization_run_id": v.optimization_run_id,
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "promoted_at": v.promoted_at.isoformat() if v.promoted_at else None,
    }


def get_program_detail(name: str) -> Optional[Dict[str, Any]]:
    db = SessionLocal()
    try:
        p = get_program(db, name)
        if p is None:
            return None
        versions = (
            db.query(PromptVersion)
            .filter_by(program_id=p.id)
            .order_by(PromptVersion.version_no.desc())
            .all()
        )
        runs = (
            db.query(PromptOptimizationRun)
            .filter_by(program_id=p.id)
            .order_by(PromptOptimizationRun.created_at.desc())
            .limit(20)
            .all()
        )
        return {
            "program": {
                "id": p.id,
                "name": p.name,
                "signature_name": p.signature_name,
                "description": p.description,
                "current_version_id": p.current_version_id,
                "usable_trace_count": count_usable_traces(db, p.id),
            },
            "versions": [_version_row(v) for v in versions],
            "runs": [run_row(r) for r in runs],
        }
    finally:
        db.close()


def run_row(r: PromptOptimizationRun) -> Dict[str, Any]:
    return {
        "id": r.id,
        "program_id": r.program_id,
        "optimizer": r.optimizer,
        "status": r.status,
        "trainset_size": r.trainset_size,
        "base_score": r.base_score,
        "best_score": r.best_score,
        "candidates": json.loads(r.candidates_json or "[]"),
        "error": r.error,
        "log": r.log,
        "started_by": r.started_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
    }


def get_run(run_id: int) -> Optional[Dict[str, Any]]:
    db = SessionLocal()
    try:
        r = db.query(PromptOptimizationRun).filter_by(id=run_id).first()
        return run_row(r) if r else None
    finally:
        db.close()
