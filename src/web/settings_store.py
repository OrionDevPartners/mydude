"""Persisted application settings (capability toggles, local-node endpoints, …).

These are non-secret feature flags / tuning values stored in the ``app_settings``
table and synced into the process environment at boot, so the rest of the app
keeps reading them through the normal env-based config path (e.g. PolicyEngine's
``ENABLE_BROWSER_CAPABILITY`` flag, or the local LLM adapters' base-URL env var).
"""
import logging
import os

from src.database import SessionLocal
from src.models import AppSetting

logger = logging.getLogger(__name__)

# Static settings that are mirrored into os.environ on boot and on change.
MANAGED_SETTINGS = (
    "ENABLE_BROWSER_CAPABILITY",
    "ENABLE_SSH_CAPABILITY",
    "ENABLE_EMAIL_CAPABILITY",
)


def _local_node_env_keys() -> set:
    """Env var names that point/tune the local (exec_locus=local) model nodes.

    Derived from env_1 (config/providers.toml) so it tracks whatever local
    providers are declared: each local provider's ``base_url_env`` plus its
    ``<KEY>_PROBE_TIMEOUT`` override, and the shared ``LOCAL_PROBE_TIMEOUT``.
    Operators set these from the dashboard (Mesh node config) instead of editing
    Replit Secrets, and they must survive restarts like any other managed setting.
    """
    keys = {"LOCAL_PROBE_TIMEOUT"}
    try:
        from src.providers.config import load_config

        cfg = load_config()
        providers = cfg.get("providers", {}) or {}
        enabled = (cfg.get("llm", {}) or {}).get("enabled", []) or []
        for key in enabled:
            d = providers.get(key, {}) or {}
            if d.get("exec_locus") == "local":
                if d.get("base_url_env"):
                    keys.add(d["base_url_env"])
                keys.add("%s_PROBE_TIMEOUT" % key.upper())
    except Exception as e:
        logger.debug("local_node_env_keys lookup failed: %s", e)
    return keys


def _env_managed_keys() -> set:
    """All setting keys that should be mirrored into the process environment."""
    return set(MANAGED_SETTINGS) | _local_node_env_keys()


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
    if key in _env_managed_keys():
        os.environ[key] = value


def delete_setting(key: str) -> None:
    """Remove a persisted setting and, if env-managed, drop it from the process.

    Dropping the env var reverts the value to its code default (or, after the
    next restart, to whatever a Replit Secret of the same name provides).
    """
    db = SessionLocal()
    try:
        row = db.query(AppSetting).filter(AppSetting.key == key).first()
        if row:
            db.delete(row)
            db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("delete_setting(%s) failed: %s", key, e)
        raise
    finally:
        db.close()
    if key in _env_managed_keys():
        os.environ.pop(key, None)


def sync_settings_to_env() -> None:
    """Load persisted managed settings into the process environment at boot."""
    managed = list(_env_managed_keys())
    db = SessionLocal()
    try:
        rows = db.query(AppSetting).filter(AppSetting.key.in_(managed)).all()
        for row in rows:
            if row.value is not None:
                os.environ[row.key] = row.value
    except Exception as e:
        logger.warning("sync_settings_to_env failed: %s", e)
    finally:
        db.close()
