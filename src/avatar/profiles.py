"""AvatarProfile CRUD — a bot's persona + voice + avatar presentation identity.

Every mutation is audited. Provider *names* are stored; credentials never are.
``avatar_config_json`` carries provider-specific knobs (e.g. HeyGen ``avatar_id``,
``quality``) and is validated as JSON on write.
"""
import json
import logging
from datetime import datetime

from src.models import AvatarProfile, AvatarAuditLog

logger = logging.getLogger(__name__)

_KNOWN_VOICE_PROVIDERS = ("elevenlabs",)
_KNOWN_AVATAR_PROVIDERS = ("heygen", "azure", "nvidia-ace", "ace", "custom", "audio2face")


def _audit(db, action, status, detail):
    db.add(AvatarAuditLog(action=action, status=status, source="avatar-profiles",
                          detail=detail))
    db.commit()


def _parse_config(avatar_config):
    """Accept a dict or a JSON string; return a JSON string or None. Fail loud."""
    if avatar_config in (None, "", {}):
        return None
    if isinstance(avatar_config, dict):
        return json.dumps(avatar_config)
    if isinstance(avatar_config, str):
        try:
            json.loads(avatar_config)
        except (json.JSONDecodeError, ValueError):
            raise ValueError("avatar_config must be valid JSON.")
        return avatar_config
    raise ValueError("avatar_config must be a JSON object or JSON string.")


def _serialize(p):
    config = None
    if p.avatar_config_json:
        try:
            config = json.loads(p.avatar_config_json)
        except (json.JSONDecodeError, ValueError):
            config = None
    return {
        "id": p.id,
        "name": p.name,
        "persona": p.persona,
        "bot_id": p.bot_id,
        "voice_provider": p.voice_provider,
        "voice_id": p.voice_id,
        "avatar_provider": p.avatar_provider,
        "avatar_config": config,
        "disclosure_required": bool(p.disclosure_required),
        "consent_required": bool(p.consent_required),
        "active": bool(p.active),
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def _validate_providers(voice_provider, avatar_provider):
    if voice_provider and voice_provider.lower() not in _KNOWN_VOICE_PROVIDERS:
        raise ValueError(
            "Unsupported voice provider '%s'. Known: %s"
            % (voice_provider, ", ".join(_KNOWN_VOICE_PROVIDERS)))
    if avatar_provider and avatar_provider.lower() not in _KNOWN_AVATAR_PROVIDERS:
        raise ValueError(
            "Unsupported avatar provider '%s'. Known: %s"
            % (avatar_provider, ", ".join(_KNOWN_AVATAR_PROVIDERS)))


def list_profiles(db, limit=100):
    rows = (db.query(AvatarProfile)
            .order_by(AvatarProfile.id.desc())
            .limit(int(limit)).all())
    return [_serialize(r) for r in rows]


def get_profile(db, profile_id):
    p = db.query(AvatarProfile).filter(AvatarProfile.id == profile_id).first()
    if p is None:
        raise ValueError("Avatar profile %s not found." % profile_id)
    return _serialize(p)


def create_profile(db, name, persona=None, voice_provider="elevenlabs", voice_id=None,
                   avatar_provider=None, avatar_config=None, disclosure_required=True,
                   consent_required=True, bot_id=None):
    if not name or not name.strip():
        raise ValueError("A profile name is required.")
    name = name.strip()
    voice_provider = (voice_provider or None)
    avatar_provider = (avatar_provider or None)
    _validate_providers(voice_provider, avatar_provider)
    config_json = _parse_config(avatar_config)
    existing = db.query(AvatarProfile).filter(AvatarProfile.name == name).first()
    if existing is not None:
        raise ValueError("An avatar profile named '%s' already exists." % name)
    p = AvatarProfile(
        name=name,
        persona=(persona or None),
        voice_provider=voice_provider,
        voice_id=(voice_id or None),
        avatar_provider=avatar_provider,
        avatar_config_json=config_json,
        disclosure_required=bool(disclosure_required),
        consent_required=bool(consent_required),
        bot_id=bot_id,
        active=True,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    _audit(db, "profile_created", "ok", "Avatar profile '%s' (#%d) created." % (name, p.id))
    return _serialize(p)


def update_profile(db, profile_id, **fields):
    p = db.query(AvatarProfile).filter(AvatarProfile.id == profile_id).first()
    if p is None:
        raise ValueError("Avatar profile %s not found." % profile_id)

    if "name" in fields and fields["name"] is not None:
        new_name = str(fields["name"]).strip()
        if not new_name:
            raise ValueError("A profile name cannot be empty.")
        clash = (db.query(AvatarProfile)
                 .filter(AvatarProfile.name == new_name, AvatarProfile.id != p.id)
                 .first())
        if clash is not None:
            raise ValueError("An avatar profile named '%s' already exists." % new_name)
        p.name = new_name

    voice_provider = fields.get("voice_provider", p.voice_provider)
    avatar_provider = fields.get("avatar_provider", p.avatar_provider)
    _validate_providers(voice_provider, avatar_provider)

    for attr in ("persona", "voice_provider", "voice_id", "avatar_provider"):
        if attr in fields:
            val = fields[attr]
            setattr(p, attr, (val or None) if isinstance(val, str) else val)
    if "avatar_config" in fields:
        p.avatar_config_json = _parse_config(fields["avatar_config"])
    for attr in ("disclosure_required", "consent_required", "active"):
        if attr in fields and fields[attr] is not None:
            setattr(p, attr, bool(fields[attr]))
    if "bot_id" in fields:
        p.bot_id = fields["bot_id"]

    p.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(p)
    _audit(db, "profile_updated", "ok", "Avatar profile #%d updated." % p.id)
    return _serialize(p)


def delete_profile(db, profile_id):
    p = db.query(AvatarProfile).filter(AvatarProfile.id == profile_id).first()
    if p is None:
        raise ValueError("Avatar profile %s not found." % profile_id)
    name = p.name
    db.delete(p)
    db.commit()
    _audit(db, "profile_deleted", "ok", "Avatar profile '%s' (#%d) deleted." % (name, profile_id))
    return {"ok": True, "id": profile_id}
