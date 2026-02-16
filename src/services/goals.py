import logging
from datetime import datetime
from src.database import SessionLocal
from src.models import Goal

logger = logging.getLogger(__name__)

def create_goal(user_id: int, objective: str) -> int:
    session = SessionLocal()
    try:
        goal = Goal(user_id=user_id, objective=objective)
        session.add(goal)
        session.commit()
        return goal.id
    finally:
        session.close()

def update_goal_progress(goal_id: int, status: str = None, progress_pct: int = None, last_result: str = None, wave_count: int = None):
    session = SessionLocal()
    try:
        goal = session.query(Goal).filter(Goal.id == goal_id).first()
        if not goal:
            return False
        if status:
            goal.status = status
        if progress_pct is not None:
            goal.progress_pct = progress_pct
        if last_result:
            goal.last_result = last_result[:5000]
        if wave_count is not None:
            goal.wave_count = wave_count
        goal.updated_at = datetime.utcnow()
        session.commit()
        return True
    finally:
        session.close()

def get_active_goals(user_id: int):
    session = SessionLocal()
    try:
        results = session.query(Goal).filter(Goal.user_id == user_id, Goal.status == "active").order_by(Goal.created_at.desc()).all()
        return [{"id": g.id, "objective": g.objective, "status": g.status, "progress_pct": g.progress_pct, "wave_count": g.wave_count, "created_at": g.created_at.isoformat() if g.created_at else ""} for g in results]
    finally:
        session.close()

def get_all_goals(user_id: int, limit: int = 20):
    session = SessionLocal()
    try:
        results = session.query(Goal).filter(Goal.user_id == user_id).order_by(Goal.created_at.desc()).limit(limit).all()
        return [{"id": g.id, "objective": g.objective, "status": g.status, "progress_pct": g.progress_pct, "wave_count": g.wave_count, "last_result": g.last_result[:200] if g.last_result else "", "created_at": g.created_at.isoformat() if g.created_at else "", "updated_at": g.updated_at.isoformat() if g.updated_at else ""} for g in results]
    finally:
        session.close()

def get_goal_by_id(goal_id: int):
    session = SessionLocal()
    try:
        g = session.query(Goal).filter(Goal.id == goal_id).first()
        if not g:
            return None
        return {"id": g.id, "user_id": g.user_id, "objective": g.objective, "status": g.status, "progress_pct": g.progress_pct, "wave_count": g.wave_count, "last_result": g.last_result, "created_at": g.created_at.isoformat() if g.created_at else "", "updated_at": g.updated_at.isoformat() if g.updated_at else ""}
    finally:
        session.close()

def complete_goal(goal_id: int):
    return update_goal_progress(goal_id, status="completed", progress_pct=100)

def cancel_goal(goal_id: int):
    return update_goal_progress(goal_id, status="cancelled")
