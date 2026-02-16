import json
import logging
from src.database import SessionLocal
from src.models import PipelineTrigger

logger = logging.getLogger(__name__)

def create_pipeline(user_id: int, trigger_command: str, actions: list) -> int:
    session = SessionLocal()
    try:
        pipe = PipelineTrigger(
            user_id=user_id,
            trigger_command=trigger_command,
            actions_json=json.dumps(actions),
        )
        session.add(pipe)
        session.commit()
        return pipe.id
    finally:
        session.close()

def get_user_pipelines(user_id: int):
    session = SessionLocal()
    try:
        pipes = session.query(PipelineTrigger).filter(PipelineTrigger.user_id == user_id).all()
        return [{"id": p.id, "trigger_command": p.trigger_command, "actions": json.loads(p.actions_json) if p.actions_json else [], "enabled": p.enabled, "created_at": p.created_at.isoformat() if p.created_at else ""} for p in pipes]
    finally:
        session.close()

def delete_pipeline(pipeline_id: int, user_id: int) -> bool:
    session = SessionLocal()
    try:
        pipe = session.query(PipelineTrigger).filter(PipelineTrigger.id == pipeline_id, PipelineTrigger.user_id == user_id).first()
        if pipe:
            session.delete(pipe)
            session.commit()
            return True
        return False
    finally:
        session.close()

def toggle_pipeline(pipeline_id: int, user_id: int) -> str:
    session = SessionLocal()
    try:
        pipe = session.query(PipelineTrigger).filter(PipelineTrigger.id == pipeline_id, PipelineTrigger.user_id == user_id).first()
        if not pipe:
            return "Pipeline not found."
        pipe.enabled = not pipe.enabled
        session.commit()
        return f"Pipeline #{pipeline_id} {'enabled' if pipe.enabled else 'disabled'}."
    finally:
        session.close()

def get_triggers_for_command(user_id: int, command: str):
    session = SessionLocal()
    try:
        pipes = session.query(PipelineTrigger).filter(
            PipelineTrigger.user_id == user_id,
            PipelineTrigger.trigger_command == command,
            PipelineTrigger.enabled == True
        ).all()
        return [{"id": p.id, "actions": json.loads(p.actions_json) if p.actions_json else []} for p in pipes]
    finally:
        session.close()
