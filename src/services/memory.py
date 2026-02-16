import logging
from datetime import datetime
from src.database import SessionLocal
from src.models import ConversationMemory

logger = logging.getLogger(__name__)

def store_memory(user_id: int, source: str, content: str, summary: str = "", entities: str = ""):
    """Store a conversation memory entry."""
    try:
        session = SessionLocal()
        try:
            entry = ConversationMemory(
                user_id=user_id,
                source=source,
                content=content[:10000] if content else "",
                summary=summary[:2000] if summary else "",
                entities=entities[:1000] if entities else "",
            )
            session.add(entry)
            session.commit()
            return entry.id
        finally:
            session.close()
    except Exception as e:
        logger.warning(f"Failed to store memory: {e}")
        return None

def search_memory(user_id: int, query: str, limit: int = 10):
    """Search memories by content or entities."""
    session = SessionLocal()
    try:
        results = session.query(ConversationMemory).filter(
            ConversationMemory.user_id == user_id,
            (ConversationMemory.content.ilike(f"%{query}%")) |
            (ConversationMemory.summary.ilike(f"%{query}%")) |
            (ConversationMemory.entities.ilike(f"%{query}%"))
        ).order_by(ConversationMemory.created_at.desc()).limit(limit).all()
        return [{"id": r.id, "source": r.source, "summary": r.summary or r.content[:200], "entities": r.entities, "created_at": r.created_at.isoformat() if r.created_at else ""} for r in results]
    finally:
        session.close()

def get_recent_memories(user_id: int, source: str = None, limit: int = 10):
    """Get recent memories, optionally filtered by source."""
    session = SessionLocal()
    try:
        q = session.query(ConversationMemory).filter(ConversationMemory.user_id == user_id)
        if source:
            q = q.filter(ConversationMemory.source == source)
        results = q.order_by(ConversationMemory.created_at.desc()).limit(limit).all()
        return [{"id": r.id, "source": r.source, "summary": r.summary or r.content[:200], "entities": r.entities, "created_at": r.created_at.isoformat() if r.created_at else ""} for r in results]
    finally:
        session.close()

def get_memory_by_id(memory_id: int):
    """Get a full memory entry by ID."""
    session = SessionLocal()
    try:
        r = session.query(ConversationMemory).filter(ConversationMemory.id == memory_id).first()
        if r:
            return {"id": r.id, "source": r.source, "content": r.content, "summary": r.summary, "entities": r.entities, "created_at": r.created_at.isoformat() if r.created_at else ""}
        return None
    finally:
        session.close()

def get_memory_stats(user_id: int):
    """Get memory statistics for a user."""
    session = SessionLocal()
    try:
        from sqlalchemy import func
        total = session.query(func.count(ConversationMemory.id)).filter(ConversationMemory.user_id == user_id).scalar() or 0
        by_source = session.query(ConversationMemory.source, func.count(ConversationMemory.id)).filter(ConversationMemory.user_id == user_id).group_by(ConversationMemory.source).all()
        return {"total": total, "by_source": {s: c for s, c in by_source}}
    finally:
        session.close()
