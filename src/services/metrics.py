import logging
from datetime import datetime, timedelta
from src.database import SessionLocal
from src.models import ProviderMetric

logger = logging.getLogger(__name__)

def record_metric(provider: str, model: str, prompt_type: str, latency_ms: int, success: bool, token_count: int = None, rating: float = None):
    """Record a provider performance metric."""
    try:
        session = SessionLocal()
        try:
            metric = ProviderMetric(
                provider=provider,
                model=model,
                prompt_type=prompt_type,
                latency_ms=latency_ms,
                success=success,
                token_count=token_count,
                rating=rating,
            )
            session.add(metric)
            session.commit()
        finally:
            session.close()
    except Exception as e:
        logger.warning(f"Failed to record metric: {e}")

def get_provider_stats(days: int = 7):
    """Get aggregated provider statistics."""
    session = SessionLocal()
    try:
        from sqlalchemy import func
        cutoff = datetime.utcnow() - timedelta(days=days)

        stats = {}
        providers = session.query(ProviderMetric.provider).distinct().all()

        for (provider,) in providers:
            q = session.query(ProviderMetric).filter(
                ProviderMetric.provider == provider,
                ProviderMetric.created_at >= cutoff
            )
            total = q.count()
            successes = q.filter(ProviderMetric.success == True).count()
            avg_latency = session.query(func.avg(ProviderMetric.latency_ms)).filter(
                ProviderMetric.provider == provider,
                ProviderMetric.created_at >= cutoff,
                ProviderMetric.success == True
            ).scalar()
            avg_rating = session.query(func.avg(ProviderMetric.rating)).filter(
                ProviderMetric.provider == provider,
                ProviderMetric.created_at >= cutoff,
                ProviderMetric.rating != None
            ).scalar()

            stats[provider] = {
                "total_calls": total,
                "success_rate": round(successes / total * 100, 1) if total > 0 else 0,
                "avg_latency_ms": int(avg_latency) if avg_latency else 0,
                "avg_rating": round(float(avg_rating), 2) if avg_rating else None,
            }
        return stats
    finally:
        session.close()

def get_provider_weights(days: int = 7) -> dict:
    """Calculate dynamic weights based on performance."""
    stats = get_provider_stats(days)
    if not stats:
        return {"openai": 1.0, "anthropic": 1.0, "gemini": 1.0, "grok": 1.0}

    weights = {}
    for provider, s in stats.items():
        score = s["success_rate"] / 100.0
        if s["avg_latency_ms"] > 0:
            speed_factor = min(1.0, 3000 / s["avg_latency_ms"])
            score = score * 0.7 + speed_factor * 0.3
        if s.get("avg_rating"):
            score = score * 0.6 + s["avg_rating"] * 0.4
        weights[provider] = round(max(0.1, score), 2)

    return weights
