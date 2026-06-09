"""Structured error surfacing for the swarm's silent-failure paths.

Several critical paths historically caught all exceptions and only logged a
warning (capability contract violations, run indexing, governance proposal
raising). That prevents cascading failures but also hides bugs operators need
to see. These helpers surface those failures into the Governance Center instead
of letting them disappear into the logs:

  * contract violations become ``SentinelEvent`` rows (visible as alerts), and
  * recoverable-but-noteworthy failures increment durable counters in the
    ``app_settings`` table that the Governance Center dashboard reads.

Every helper here is best-effort: a metrics-store outage must never crash the
swarm path that is merely *reporting* an error, so persistence failures are
swallowed (logged only).
"""
import logging
import uuid

from src.database import SessionLocal
from src.models import AppSetting, SentinelEvent

logger = logging.getLogger(__name__)

# Counter keys persisted in the app_settings table.
METRIC_FAILED_INDEXES = "metric_failed_indexes"
METRIC_GOVERNANCE_PROPOSAL_FAILURES = "metric_governance_proposal_failures"


def increment_metric(key: str, amount: int = 1) -> None:
    """Atomically bump an integer counter stored in app_settings."""
    db = SessionLocal()
    try:
        row = db.query(AppSetting).filter(AppSetting.key == key).first()
        if row is None:
            db.add(AppSetting(key=key, value=str(amount)))
        else:
            try:
                current = int(row.value or 0)
            except (ValueError, TypeError):
                current = 0
            row.value = str(current + amount)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("increment_metric(%s) failed: %s", key, e)
    finally:
        db.close()


def get_metric(key: str) -> int:
    """Read an integer counter from app_settings (0 if unset/unparseable)."""
    db = SessionLocal()
    try:
        row = db.query(AppSetting).filter(AppSetting.key == key).first()
        if row and row.value is not None:
            try:
                return int(row.value)
            except (ValueError, TypeError):
                return 0
        return 0
    except Exception as e:
        logger.warning("get_metric(%s) failed: %s", key, e)
        return 0
    finally:
        db.close()


def record_sentinel_event(
    alert_type: str,
    severity: str,
    description: str,
    recommended_action: str = "",
    alert_id: str = "",
) -> None:
    """Persist a SentinelEvent row so the failure shows up in the Governance Center."""
    db = SessionLocal()
    try:
        ev = SentinelEvent(
            alert_id=alert_id or f"ALERT-{uuid.uuid4().hex[:8].upper()}",
            alert_type=alert_type,
            severity=severity,
            description=description,
            recommended_action=recommended_action,
        )
        db.add(ev)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("record_sentinel_event(%s) failed: %s", alert_type, e)
    finally:
        db.close()
