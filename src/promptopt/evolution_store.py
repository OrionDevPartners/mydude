"""Persistence layer for the edge-truth / thesis self-evolution loop.

All DB operations for CognitionComponent, CognitionThesis, ThesisTrialIteration,
and EvolutionCycleLog. Kept separate from evolution.py (the engine) so the
store is import-safe at startup (no heavy deps).

Fail-loud contract: every function raises rather than returning a silent
fallback — callers must handle or propagate the exception.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.database import SessionLocal
from src.models import (
    CognitionComponent,
    CognitionThesis,
    EvolutionCycleLog,
    ThesisTrialIteration,
)


def _now() -> datetime:
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# Component CRUD
# ---------------------------------------------------------------------------

def get_component(db, name: str) -> Optional[CognitionComponent]:
    return db.query(CognitionComponent).filter_by(name=name).first()


def get_component_by_id(db, component_id: int) -> Optional[CognitionComponent]:
    return db.query(CognitionComponent).filter_by(id=component_id).first()


def list_components() -> List[Dict[str, Any]]:
    db = SessionLocal()
    try:
        rows = db.query(CognitionComponent).order_by(CognitionComponent.name).all()
        return [_component_row(c, db) for c in rows]
    finally:
        db.close()


def _component_row(c: CognitionComponent, db) -> Dict[str, Any]:
    active_thesis = (
        db.query(CognitionThesis)
        .filter(
            CognitionThesis.component_id == c.id,
            CognitionThesis.status.in_([
                "proposed", "testing", "awaiting_consensus", "awaiting_human_approval"
            ]),
        )
        .order_by(CognitionThesis.created_at.desc())
        .first()
    )
    total_theses = db.query(CognitionThesis).filter_by(component_id=c.id).count()
    promoted = db.query(CognitionThesis).filter_by(component_id=c.id, status="promoted").count()
    return {
        "id": c.id,
        "name": c.name,
        "component_type": c.component_type,
        "description": c.description,
        "truth_json": json.loads(c.truth_json or "{}"),
        "truth_version_id": c.truth_version_id,
        "loop_state": c.loop_state,
        "loop_enabled": bool(c.loop_enabled),
        "cycle_count": c.cycle_count or 0,
        "last_cycle_at": c.last_cycle_at.isoformat() if c.last_cycle_at else None,
        "active_thesis": _thesis_row(active_thesis, db) if active_thesis else None,
        "total_theses": total_theses,
        "promoted_theses": promoted,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


def ensure_component(
    name: str,
    component_type: str,
    description: str,
    truth_json: Optional[Dict[str, Any]] = None,
    truth_version_id: Optional[int] = None,
) -> int:
    """Upsert a cognition component. Returns its id."""
    db = SessionLocal()
    try:
        c = get_component(db, name)
        if c is None:
            c = CognitionComponent(
                name=name,
                component_type=component_type,
                description=description,
                truth_json=json.dumps(truth_json or {}),
                truth_version_id=truth_version_id,
            )
            db.add(c)
            db.commit()
            db.refresh(c)
        elif truth_version_id is not None and c.truth_version_id != truth_version_id:
            c.truth_version_id = truth_version_id
            c.truth_json = json.dumps(truth_json or {})
            c.updated_at = _now()
            db.commit()
        return c.id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def update_component_truth(
    component_id: int,
    truth_json: Dict[str, Any],
    truth_version_id: Optional[int] = None,
) -> None:
    db = SessionLocal()
    try:
        c = get_component_by_id(db, component_id)
        if c is None:
            raise ValueError("component %d not found" % component_id)
        c.truth_json = json.dumps(truth_json)
        if truth_version_id is not None:
            c.truth_version_id = truth_version_id
        c.updated_at = _now()
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def set_loop_state(component_id: int, state: str) -> None:
    db = SessionLocal()
    try:
        c = get_component_by_id(db, component_id)
        if c is None:
            raise ValueError("component %d not found" % component_id)
        c.loop_state = state
        c.updated_at = _now()
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def set_loop_enabled(component_id: int, enabled: bool) -> None:
    db = SessionLocal()
    try:
        c = get_component_by_id(db, component_id)
        if c is None:
            raise ValueError("component %d not found" % component_id)
        c.loop_enabled = enabled
        c.loop_state = "idle" if not enabled else c.loop_state
        c.updated_at = _now()
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def increment_cycle(component_id: int) -> int:
    db = SessionLocal()
    try:
        c = get_component_by_id(db, component_id)
        if c is None:
            raise ValueError("component %d not found" % component_id)
        c.cycle_count = (c.cycle_count or 0) + 1
        c.last_cycle_at = _now()
        c.updated_at = _now()
        db.commit()
        return c.cycle_count
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Thesis CRUD
# ---------------------------------------------------------------------------

def _thesis_row(t: CognitionThesis, db) -> Dict[str, Any]:
    iterations = (
        db.query(ThesisTrialIteration)
        .filter_by(thesis_id=t.id)
        .order_by(ThesisTrialIteration.iteration_no.asc())
        .all()
    )
    return {
        "id": t.id,
        "component_id": t.component_id,
        "branch_cell": t.branch_cell,
        "thesis": json.loads(t.thesis_json or "{}"),
        "rationale": t.rationale,
        "status": t.status,
        "test_score": t.test_score,
        "base_score": t.base_score,
        "governance_proposal_id": t.governance_proposal_id,
        "governance_proposal_db_id": t.governance_proposal_db_id,
        "requires_human_gate": bool(t.requires_human_gate),
        "trial_iteration_count": t.trial_iteration_count or 0,
        "stalled_at": t.stalled_at.isoformat() if t.stalled_at else None,
        "cycle_index": t.cycle_index or 0,
        "selection_votes": json.loads(t.selection_votes_json or "{}"),
        "iterations": [_iteration_row(i) for i in iterations],
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


def _iteration_row(i: ThesisTrialIteration) -> Dict[str, Any]:
    return {
        "id": i.id,
        "iteration_no": i.iteration_no,
        "sandbox_label": i.sandbox_label,
        "test_results": json.loads(i.test_results_json or "{}"),
        "compliance_score": i.compliance_score,
        "hallucination_risk": i.hallucination_risk,
        "composite_score": i.composite_score,
        "all_tests_passed": bool(i.all_tests_passed),
        "outcome": i.outcome,
        "error": i.error,
        "created_at": i.created_at.isoformat() if i.created_at else None,
    }


def create_thesis(
    component_id: int,
    branch_cell: str,
    thesis: Dict[str, Any],
    rationale: str,
    cycle_index: int = 0,
    requires_human_gate: bool = False,
    selection_votes: Optional[Dict[str, Any]] = None,
) -> int:
    db = SessionLocal()
    try:
        t = CognitionThesis(
            component_id=component_id,
            branch_cell=branch_cell,
            thesis_json=json.dumps(thesis),
            rationale=rationale,
            status="proposed",
            cycle_index=cycle_index,
            requires_human_gate=requires_human_gate,
            selection_votes_json=json.dumps(selection_votes or {}),
        )
        db.add(t)
        db.commit()
        db.refresh(t)
        return t.id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def update_thesis_status(
    thesis_id: int,
    status: str,
    test_score: Optional[float] = None,
    base_score: Optional[float] = None,
    governance_proposal_id: Optional[str] = None,
    governance_proposal_db_id: Optional[int] = None,
) -> None:
    db = SessionLocal()
    try:
        t = db.query(CognitionThesis).filter_by(id=thesis_id).first()
        if t is None:
            raise ValueError("thesis %d not found" % thesis_id)
        t.status = status
        if test_score is not None:
            t.test_score = test_score
        if base_score is not None:
            t.base_score = base_score
        if governance_proposal_id is not None:
            t.governance_proposal_id = governance_proposal_id
        if governance_proposal_db_id is not None:
            t.governance_proposal_db_id = governance_proposal_db_id
        if status == "stalled":
            t.stalled_at = _now()
        t.updated_at = _now()
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def increment_thesis_iterations(thesis_id: int) -> int:
    db = SessionLocal()
    try:
        t = db.query(CognitionThesis).filter_by(id=thesis_id).first()
        if t is None:
            raise ValueError("thesis %d not found" % thesis_id)
        t.trial_iteration_count = (t.trial_iteration_count or 0) + 1
        t.updated_at = _now()
        db.commit()
        return t.trial_iteration_count
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_thesis(thesis_id: int) -> Optional[Dict[str, Any]]:
    db = SessionLocal()
    try:
        t = db.query(CognitionThesis).filter_by(id=thesis_id).first()
        if t is None:
            return None
        return _thesis_row(t, db)
    finally:
        db.close()


def list_theses(
    component_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    db = SessionLocal()
    try:
        q = db.query(CognitionThesis)
        if component_id is not None:
            q = q.filter_by(component_id=component_id)
        if status is not None:
            q = q.filter_by(status=status)
        rows = q.order_by(CognitionThesis.created_at.desc()).limit(limit).all()
        return [_thesis_row(t, db) for t in rows]
    finally:
        db.close()


def get_active_thesis(component_id: int) -> Optional[Dict[str, Any]]:
    db = SessionLocal()
    try:
        t = (
            db.query(CognitionThesis)
            .filter(
                CognitionThesis.component_id == component_id,
                CognitionThesis.status.in_([
                    "proposed", "testing", "awaiting_consensus", "awaiting_human_approval"
                ]),
            )
            .order_by(CognitionThesis.created_at.desc())
            .first()
        )
        if t is None:
            return None
        return _thesis_row(t, db)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Trial iteration
# ---------------------------------------------------------------------------

def record_iteration(
    thesis_id: int,
    iteration_no: int,
    test_results: Dict[str, Any],
    compliance_score: Optional[float],
    hallucination_risk: Optional[float],
    composite_score: Optional[float],
    all_tests_passed: bool,
    outcome: str,
    error: Optional[str] = None,
) -> int:
    db = SessionLocal()
    try:
        i = ThesisTrialIteration(
            thesis_id=thesis_id,
            iteration_no=iteration_no,
            sandbox_label="EXPERIMENTAL",
            test_results_json=json.dumps(test_results),
            compliance_score=compliance_score,
            hallucination_risk=hallucination_risk,
            composite_score=composite_score,
            all_tests_passed=all_tests_passed,
            outcome=outcome,
            error=(error or "")[:2000] if error else None,
        )
        db.add(i)
        db.commit()
        db.refresh(i)
        return i.id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Cycle log
# ---------------------------------------------------------------------------

def log_cycle(
    component_id: int,
    cycle_index: int,
    outcome: str,
    thesis_id: Optional[int] = None,
    next_selection: Optional[Dict[str, Any]] = None,
    detail: Optional[str] = None,
) -> int:
    db = SessionLocal()
    try:
        log = EvolutionCycleLog(
            component_id=component_id,
            cycle_index=cycle_index,
            outcome=outcome,
            thesis_id=thesis_id,
            next_thesis_selection_json=json.dumps(next_selection or {}),
            detail=(detail or "")[:2000],
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        return log.id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_recent_stalled_branch_cells(
    component_id: int,
    last_n_cycles: int = 10,
) -> Dict[str, int]:
    """Return {branch_cell: stall_count} over the last N cycle logs.

    A stall signal is an EvolutionCycleLog whose outcome is 'stalled' and which
    recorded the failing branch cell under the 'stalled_branch_cell' key of its
    next_thesis_selection_json. Used by select_next_thesis to weight down branch
    cells that have recently stalled so the loop learns from the stall signal
    instead of blindly re-selecting the same dead-end cell.

    Only the most recent ``last_n_cycles`` cycle logs are scanned so the negative
    signal decays as fresh (non-stalling) cycles push old stalls out of the window.
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(EvolutionCycleLog)
            .filter_by(component_id=component_id)
            .order_by(EvolutionCycleLog.created_at.desc())
            .limit(last_n_cycles)
            .all()
        )
        counts: Dict[str, int] = {}
        for r in rows:
            if r.outcome != "stalled":
                continue
            try:
                sel = json.loads(r.next_thesis_selection_json or "{}")
            except Exception:
                sel = {}
            cell = sel.get("stalled_branch_cell")
            if cell:
                counts[cell] = counts.get(cell, 0) + 1
        return counts
    finally:
        db.close()


def list_cycle_logs(component_id: int, limit: int = 30) -> List[Dict[str, Any]]:
    db = SessionLocal()
    try:
        rows = (
            db.query(EvolutionCycleLog)
            .filter_by(component_id=component_id)
            .order_by(EvolutionCycleLog.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "cycle_index": r.cycle_index,
                "outcome": r.outcome,
                "thesis_id": r.thesis_id,
                "next_selection": json.loads(r.next_thesis_selection_json or "{}"),
                "detail": r.detail,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    finally:
        db.close()


def get_component_status(component_id: int) -> Optional[Dict[str, Any]]:
    """Lightweight status snapshot for cheap polling.

    Returns only counters + a change signature for the active thesis / latest
    cycle log — no JSON payload parsing, no per-iteration row hydration — so the
    dashboard can poll it every few seconds without the cost of a full detail
    fetch. Returns None when the component does not exist.
    """
    db = SessionLocal()
    try:
        c = get_component_by_id(db, component_id)
        if c is None:
            return None
        active = (
            db.query(CognitionThesis)
            .filter(
                CognitionThesis.component_id == c.id,
                CognitionThesis.status.in_([
                    "proposed", "testing", "awaiting_consensus", "awaiting_human_approval"
                ]),
            )
            .order_by(CognitionThesis.created_at.desc())
            .first()
        )
        active_thesis_id = active.id if active else None
        active_thesis_status = active.status if active else None
        active_thesis_iterations = (
            db.query(ThesisTrialIteration).filter_by(thesis_id=active.id).count()
            if active else 0
        )
        latest_log = (
            db.query(EvolutionCycleLog)
            .filter_by(component_id=c.id)
            .order_by(EvolutionCycleLog.created_at.desc())
            .first()
        )
        total_theses = db.query(CognitionThesis).filter_by(component_id=c.id).count()
        promoted = db.query(CognitionThesis).filter_by(component_id=c.id, status="promoted").count()
        return {
            "id": c.id,
            "loop_state": c.loop_state,
            "cycle_count": c.cycle_count or 0,
            "last_cycle_at": c.last_cycle_at.isoformat() if c.last_cycle_at else None,
            "total_theses": total_theses,
            "promoted_theses": promoted,
            "active_thesis_id": active_thesis_id,
            "active_thesis_status": active_thesis_status,
            "active_thesis_iterations": active_thesis_iterations,
            "latest_cycle_log_id": latest_log.id if latest_log else None,
        }
    finally:
        db.close()


def get_component_detail(component_id: int) -> Optional[Dict[str, Any]]:
    db = SessionLocal()
    try:
        c = get_component_by_id(db, component_id)
        if c is None:
            return None
        theses = (
            db.query(CognitionThesis)
            .filter_by(component_id=c.id)
            .order_by(CognitionThesis.created_at.desc())
            .limit(20)
            .all()
        )
        logs = (
            db.query(EvolutionCycleLog)
            .filter_by(component_id=c.id)
            .order_by(EvolutionCycleLog.created_at.desc())
            .limit(20)
            .all()
        )
        return {
            "component": _component_row(c, db),
            "theses": [_thesis_row(t, db) for t in theses],
            "cycle_logs": [
                {
                    "id": r.id,
                    "cycle_index": r.cycle_index,
                    "outcome": r.outcome,
                    "thesis_id": r.thesis_id,
                    "next_selection": json.loads(r.next_thesis_selection_json or "{}"),
                    "detail": r.detail,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in logs
            ],
        }
    finally:
        db.close()
