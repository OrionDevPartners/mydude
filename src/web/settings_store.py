"""Persisted application settings (capability toggles, etc.).

These are non-secret feature flags stored in the ``app_settings`` table and
synced into the process environment at boot, so the rest of the app keeps
reading them through the normal env-based config path (e.g. PolicyEngine's
``ENABLE_BROWSER_CAPABILITY`` / ``ENABLE_SSH_CAPABILITY`` flags).
"""
import logging
import os

from src.database import SessionLocal
from src.models import AppSetting

logger = logging.getLogger(__name__)

# Settings that are mirrored into os.environ on boot and on change.
MANAGED_SETTINGS = ("ENABLE_BROWSER_CAPABILITY", "ENABLE_SSH_CAPABILITY")


def get_setting(key: str, default=None):
    db = SessionLocal()
    try:
        row = db.query(AppSetting).filter(AppSetting.key == key).first()
        return row.value if row and row.value is not None else default
    finally:
        db.close()


def set_setting(key: str, value: str) -> None:
    """Persist a setting and, if it is environment-managed, apply it now."""
    db = SessionLocal()
    try:
        row = db.query(AppSetting).filter(AppSetting.key == key).first()
        if row:
            row.value = value
        else:
            db.add(AppSetting(key=key, value=value))
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("set_setting(%s) failed: %s", key, e)
        raise
    finally:
        db.close()
    if key in MANAGED_SETTINGS:
        os.environ[key] = value


def sync_settings_to_env() -> None:
    """Load persisted managed settings into the process environment at boot."""
    db = SessionLocal()
    try:
        rows = db.query(AppSetting).filter(AppSetting.key.in_(MANAGED_SETTINGS)).all()
        for row in rows:
            if row.value is not None:
                os.environ[row.key] = row.value
    except Exception as e:
        logger.warning("sync_settings_to_env failed: %s", e)
    finally:
        db.close()
