import logging
from datetime import datetime
from src.database import SessionLocal
from src.models import AuditLog

logger = logging.getLogger(__name__)

def log_command(user_id: int, command: str, args: str = "", status: str = "ok", output_preview: str = ""):
    """Log a command execution to the audit trail."""
    try:
        session = SessionLocal()
        try:
            entry = AuditLog(
                user_id=user_id,
                command=command,
                args=args[:2000] if args else "",
                status=status,
                output_preview=output_preview[:500] if output_preview else "",
            )
            session.add(entry)
            session.commit()
        finally:
            session.close()
    except Exception as e:
        logger.warning(f"Failed to log audit entry: {e}")

def get_audit_log(user_id: int = None, limit: int = 50, command_filter: str = None):
    """Retrieve audit log entries."""
    session = SessionLocal()
    try:
        q = session.query(AuditLog)
        if user_id:
            q = q.filter(AuditLog.user_id == user_id)
        if command_filter:
            q = q.filter(AuditLog.command.ilike(f"%{command_filter}%"))
        q = q.order_by(AuditLog.created_at.desc()).limit(limit)
        results = q.all()
        return [{"id": r.id, "user_id": r.user_id, "command": r.command, "args": r.args, "status": r.status, "output_preview": r.output_preview, "created_at": r.created_at.isoformat() if r.created_at else ""} for r in results]
    finally:
        session.close()

def search_audit(query: str, limit: int = 20):
    """Search audit log by command or args content."""
    session = SessionLocal()
    try:
        results = session.query(AuditLog).filter(
            (AuditLog.command.ilike(f"%{query}%")) | (AuditLog.args.ilike(f"%{query}%"))
        ).order_by(AuditLog.created_at.desc()).limit(limit).all()
        return [{"id": r.id, "user_id": r.user_id, "command": r.command, "args": r.args[:100], "status": r.status, "created_at": r.created_at.isoformat() if r.created_at else ""} for r in results]
    finally:
        session.close()
