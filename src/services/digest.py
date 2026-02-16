import asyncio
import logging
from datetime import datetime
from src.database import SessionLocal
from src.models import Task, Goal, ConversationMemory, DigestConfig, AuditLog

logger = logging.getLogger(__name__)

def get_digest_config(user_id: int):
    session = SessionLocal()
    try:
        config = session.query(DigestConfig).filter(DigestConfig.user_id == user_id).first()
        if config:
            return {"frequency": config.frequency, "hour_utc": config.hour_utc, "day_of_week": config.day_of_week, "enabled": config.enabled, "last_sent": config.last_sent.isoformat() if config.last_sent else "never"}
        return None
    finally:
        session.close()

def set_digest_config(user_id: int, frequency: str = "daily", hour_utc: int = 9):
    session = SessionLocal()
    try:
        config = session.query(DigestConfig).filter(DigestConfig.user_id == user_id).first()
        if not config:
            config = DigestConfig(user_id=user_id, frequency=frequency, hour_utc=hour_utc, enabled=True)
            session.add(config)
        else:
            config.frequency = frequency
            config.hour_utc = hour_utc
            config.enabled = True
        session.commit()
        return True
    finally:
        session.close()

def toggle_digest(user_id: int) -> bool:
    session = SessionLocal()
    try:
        config = session.query(DigestConfig).filter(DigestConfig.user_id == user_id).first()
        if config:
            config.enabled = not config.enabled
            session.commit()
            return config.enabled
        return False
    finally:
        session.close()

def build_digest(user_id: int) -> str:
    """Build a digest summary for a user."""
    session = SessionLocal()
    try:
        pending_tasks = session.query(Task).filter(Task.user_id == user_id, Task.status == "pending").all()
        active_goals = session.query(Goal).filter(Goal.user_id == user_id, Goal.status == "active").all()
        recent_memories = session.query(ConversationMemory).filter(ConversationMemory.user_id == user_id).order_by(ConversationMemory.created_at.desc()).limit(5).all()
        recent_commands = session.query(AuditLog).filter(AuditLog.user_id == user_id).order_by(AuditLog.created_at.desc()).limit(10).all()
        
        lines = [f"DAILY DIGEST - {datetime.utcnow().strftime('%Y-%m-%d')}", "=" * 40, ""]
        
        lines.append(f"PENDING TASKS ({len(pending_tasks)})")
        lines.append("-" * 30)
        if pending_tasks:
            for t in pending_tasks[:10]:
                priority = f" [{t.priority}]" if t.priority != "medium" else ""
                lines.append(f"  #{t.id}: {t.title}{priority}")
        else:
            lines.append("  No pending tasks")
        lines.append("")
        
        lines.append(f"ACTIVE GOALS ({len(active_goals)})")
        lines.append("-" * 30)
        if active_goals:
            for g in active_goals[:5]:
                lines.append(f"  #{g.id}: {g.objective[:60]} ({g.progress_pct}%)")
        else:
            lines.append("  No active goals")
        lines.append("")
        
        if recent_memories:
            lines.append(f"RECENT INSIGHTS ({len(recent_memories)})")
            lines.append("-" * 30)
            for m in recent_memories:
                lines.append(f"  [{m.source}] {(m.summary or m.content)[:80]}")
            lines.append("")
        
        lines.append(f"RECENT ACTIVITY")
        lines.append("-" * 30)
        if recent_commands:
            for c in recent_commands[:5]:
                lines.append(f"  [{c.created_at.strftime('%H:%M') if c.created_at else '??'}] /{c.command} ({c.status})")
        else:
            lines.append("  No recent activity")
        
        return "\n".join(lines)
    finally:
        session.close()

def get_due_digests():
    """Get digest configs that are due to send."""
    session = SessionLocal()
    try:
        now = datetime.utcnow()
        current_hour = now.hour
        configs = session.query(DigestConfig).filter(DigestConfig.enabled == True, DigestConfig.hour_utc == current_hour).all()
        due = []
        for c in configs:
            if c.last_sent and c.last_sent.date() == now.date():
                continue
            if c.frequency == "weekly" and now.weekday() != c.day_of_week:
                continue
            due.append({"user_id": c.user_id, "id": c.id})
        return due
    finally:
        session.close()

def mark_digest_sent(config_id: int):
    session = SessionLocal()
    try:
        config = session.query(DigestConfig).filter(DigestConfig.id == config_id).first()
        if config:
            config.last_sent = datetime.utcnow()
            session.commit()
    finally:
        session.close()


class DigestRunner:
    """Background digest runner."""
    
    def __init__(self, bot_app=None):
        self._running = False
        self._task = None
        self.bot_app = bot_app
    
    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("DigestRunner started")
    
    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
    
    async def _loop(self):
        while self._running:
            try:
                await asyncio.sleep(3600)
                due = get_due_digests()
                for d in due:
                    try:
                        digest_text = build_digest(d["user_id"])
                        if self.bot_app:
                            await self.bot_app.bot.send_message(chat_id=d["user_id"], text=digest_text[:4000])
                        mark_digest_sent(d["id"])
                    except Exception as e:
                        logger.warning(f"Failed to send digest to {d['user_id']}: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"DigestRunner error: {e}")
