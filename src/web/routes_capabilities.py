"""Capabilities console — status + live test panel for the browser and SSH
bridge capabilities. Every test runs through the same broker -> policy ->
integrations path the swarm uses, so the screen reflects real governance.
"""
import logging
import os
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.database import SessionLocal
from src.models import ApiKey, CapabilityAuditLog
from src.web.auth import require_auth
from src.web.crypto import encrypt_value, encryption_key_is_persistent
from src.web.templating import templates

logger = logging.getLogger(__name__)
router = APIRouter()

_TRUTHY = ("1", "true", "yes", "on")


def _flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in _TRUTHY


def _encryption_persistent() -> bool:
    return encryption_key_is_persistent()


def _broker():
    from src.swarm.broker import CapabilityBroker
    from src.swarm.integrations import Integrations
    from src.swarm.policy import PolicyEngine
    return CapabilityBroker(PolicyEngine(), Integrations())


def _browser_status():
    try:
        from src.browser.engine import BrowserEngine
        return BrowserEngine().status()
    except Exception as e:  # never let a config issue break the page
        logger.warning("Browser status failed: %s", e)
        return []


def _ssh_status():
    try:
        from src.bridge.ssh import load_ssh_config
        cfg = load_ssh_config()
        return {
            "configured": cfg.configured,
            "host": cfg.host or "",
            "user": cfg.user or "",
            "port": cfg.port,
            "auth": "private key" if cfg.private_key else ("password" if cfg.password else "none"),
            "host_verified": cfg.host_verified,
        }
    except Exception as e:
        logger.warning("SSH status failed: %s", e)
        return {"configured": False, "host": "", "user": "", "port": 22, "auth": "none",
                "host_verified": False}


def _email_status():
    try:
        from src.bridge.email_imap import load_email_config
        cfg = load_email_config()
        return {
            "configured": cfg.configured,
            "host": cfg.host or "",
            "user": cfg.user or "",
            "port": cfg.port,
            "mailbox": cfg.mailbox,
            "ssl": cfg.use_ssl,
        }
    except Exception as e:
        logger.warning("Email status failed: %s", e)
        return {"configured": False, "host": "", "user": "", "port": 993,
                "mailbox": "INBOX", "ssl": True}


def _allowlist(name: str, default):
    raw = os.environ.get(name, "")
    if not raw.strip():
        return list(default)
    return [x.strip() for x in raw.split(",") if x.strip()]


def _context(request, result=None, result_kind=None):
    db = SessionLocal()
    try:
        logs = (
            db.query(CapabilityAuditLog)
            .order_by(CapabilityAuditLog.created_at.desc())
            .limit(50)
            .all()
        )
        audit = [{
            "capability": l.capability,
            "target": l.target or "",
            "backend": l.backend or "",
            "status": l.status,
            "detail": l.detail or "",
            "source": l.source or "",
            "created_at": l.created_at,
        } for l in logs]
    finally:
        db.close()
    return {
        "request": request,
        "browser_enabled": _flag("ENABLE_BROWSER_CAPABILITY"),
        "ssh_enabled": _flag("ENABLE_SSH_CAPABILITY"),
        "email_enabled": _flag("ENABLE_EMAIL_CAPABILITY"),
        "browser_backends": _browser_status(),
        "ssh": _ssh_status(),
        "email": _email_status(),
        "browser_domains": _allowlist("BROWSER_ALLOWED_DOMAINS", ["example.com"]),
        "ssh_commands": _allowlist("SSH_ALLOWED_COMMANDS", [
            "echo", "whoami", "hostname", "uname", "sw_vers", "uptime", "date",
            "pwd", "ls", "cat", "head", "tail", "sqlite3", "defaults", "system_profiler",
        ]),
        "result": result,
        "result_kind": result_kind,
        "audit": audit,
        "encryption_persistent": _encryption_persistent(),
    }


@router.get("/capabilities", response_class=HTMLResponse)
async def capabilities_page(request: Request, _=Depends(require_auth)):
    return templates.TemplateResponse("capabilities.html", _context(request))


_TOGGLEABLE = {
    "browser": ("ENABLE_BROWSER_CAPABILITY", "Browser automation"),
    "ssh": ("ENABLE_SSH_CAPABILITY", "SSH bridge"),
    "email": ("ENABLE_EMAIL_CAPABILITY", "Email receipts"),
}


@router.post("/capabilities/toggle")
async def toggle_capability(
    request: Request,
    capability: str = Form(""),
    enabled: str = Form(""),
    _=Depends(require_auth),
):
    from urllib.parse import quote
    from src.web.settings_store import set_setting

    entry = _TOGGLEABLE.get(capability.strip())
    if not entry:
        return RedirectResponse(
            url="/capabilities?err=" + quote("Unknown capability."), status_code=303
        )
    env_var, label = entry
    on = enabled.strip().lower() in _TRUTHY
    try:
        set_setting(env_var, "true" if on else "false")
    except Exception:
        return RedirectResponse(
            url="/capabilities?err=" + quote("Could not update setting."), status_code=303
        )
    state = "enabled" if on else "disabled"
    return RedirectResponse(
        url="/capabilities?msg=" + quote("%s %s." % (label, state)), status_code=303
    )


@router.post("/capabilities/test/browser", response_class=HTMLResponse)
async def test_browser(request: Request, url: str = Form(""), _=Depends(require_auth)):
    broker = _broker()
    res = await broker.request("browser_open", {"url": url.strip(), "source": "capabilities-ui"})
    result = {
        "allowed": res.decision.allowed,
        "reason": res.decision.reason,
        "output": res.output,
        "screenshot": res.screenshot_b64,
    }
    return templates.TemplateResponse(
        "capabilities.html", _context(request, result=result, result_kind="browser")
    )


@router.post("/capabilities/test/ssh", response_class=HTMLResponse)
async def test_ssh(request: Request, command: str = Form(""), _=Depends(require_auth)):
    broker = _broker()
    res = await broker.request("ssh_run", {"command": command.strip(), "source": "capabilities-ui"})
    result = {
        "allowed": res.decision.allowed,
        "reason": res.decision.reason,
        "output": res.output,
    }
    return templates.TemplateResponse(
        "capabilities.html", _context(request, result=result, result_kind="ssh")
    )


@router.post("/capabilities/test/code", response_class=HTMLResponse)
async def test_code(request: Request, _=Depends(require_auth)):
    broker = _broker()
    res = await broker.request("ssh_fetch_code", {"source": "capabilities-ui"})
    result = {
        "allowed": res.decision.allowed,
        "reason": res.decision.reason,
        "output": res.output,
    }
    return templates.TemplateResponse(
        "capabilities.html", _context(request, result=result, result_kind="code")
    )


@router.post("/capabilities/test/history", response_class=HTMLResponse)
async def test_history(request: Request, browser: str = Form("chrome"), _=Depends(require_auth)):
    broker = _broker()
    res = await broker.request(
        "ssh_read_history", {"browser": browser, "limit": 20, "source": "capabilities-ui"}
    )
    result = {
        "allowed": res.decision.allowed,
        "reason": res.decision.reason,
        "output": res.output,
    }
    return templates.TemplateResponse(
        "capabilities.html", _context(request, result=result, result_kind="history")
    )


@router.post("/capabilities/test/receipts", response_class=HTMLResponse)
async def test_receipts(request: Request, _=Depends(require_auth)):
    broker = _broker()
    res = await broker.request(
        "imap_read_receipts",
        {"limit": 10, "lookback_days": 365, "source": "capabilities-ui"},
    )
    output = res.output
    # The raw output is a JSON array; summarise it so the panel stays readable
    # and never dumps full email bodies onto the page.
    if res.decision.allowed and output and output.startswith("["):
        try:
            import json
            from src.subscriptions.discovery import parse_receipts
            msgs = json.loads(output)
            cands = parse_receipts(output)
            names = ", ".join(sorted({c["name"] for c in cands if not c.get("unknown")})) or "none recognised"
            unknown = sum(1 for c in cands if c.get("unknown"))
            extra = (" Plus %d unrecognised billing sender(s) to review." % unknown) if unknown else ""
            output = ("Read %d recent billing email(s). Recognised services: %s.%s"
                      % (len(msgs), names, extra))
        except Exception:
            pass
    result = {
        "allowed": res.decision.allowed,
        "reason": res.decision.reason,
        "output": output,
    }
    return templates.TemplateResponse(
        "capabilities.html", _context(request, result=result, result_kind="receipts")
    )


@router.post("/capabilities/email-config")
async def save_email_config(
    request: Request,
    host: str = Form(""),
    port: str = Form("993"),
    user: str = Form(""),
    password: str = Form(""),
    mailbox: str = Form("INBOX"),
    _=Depends(require_auth),
):
    from urllib.parse import quote

    host = host.strip()
    user = user.strip()
    password = password.strip()
    mailbox = mailbox.strip() or "INBOX"
    if not host or not user:
        return RedirectResponse(
            url="/capabilities?err=" + quote("Mail host and username are required."),
            status_code=303,
        )
    db = SessionLocal()
    try:
        _upsert_vault(db, "imap-host", "IMAP_HOST", host)
        _upsert_vault(db, "imap-user", "IMAP_USER", user)
        _upsert_vault(db, "imap-port", "IMAP_PORT", (port.strip() or "993"))
        _upsert_vault(db, "imap-mailbox", "IMAP_MAILBOX", mailbox)
        if password:
            _upsert_vault(db, "imap-password", "IMAP_PASSWORD", password)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error("Saving email config failed: %s", e)
        return RedirectResponse(
            url="/capabilities?err=" + quote("Could not save email config."), status_code=303
        )
    finally:
        db.close()
    from src.web.routes_keys import sync_keys_to_env
    sync_keys_to_env()
    return RedirectResponse(
        url="/capabilities?msg=" + quote("Email bridge configuration saved to vault."),
        status_code=303,
    )


def _upsert_vault(db, provider, env_var, value):
    """Create or update a vault entry keyed by its env var, storing it encrypted."""
    existing = (
        db.query(ApiKey)
        .filter((ApiKey.env_var == env_var) | (ApiKey.provider == provider))
        .first()
    )
    if existing:
        existing.encrypted_key = encrypt_value(value)
        existing.is_active = True
        existing.env_var = env_var
        existing.last_rotated_at = datetime.utcnow()
    else:
        db.add(ApiKey(
            provider=provider,
            label=provider,
            encrypted_key=encrypt_value(value),
            is_active=True,
            category="Automation",
            env_var=env_var,
            last_rotated_at=datetime.utcnow(),
        ))


@router.post("/capabilities/ssh-config")
async def save_ssh_config(
    request: Request,
    host: str = Form(""),
    port: str = Form("22"),
    user: str = Form(""),
    private_key: str = Form(""),
    password: str = Form(""),
    host_fingerprint: str = Form(""),
    _=Depends(require_auth),
):
    from urllib.parse import quote

    host = host.strip()
    user = user.strip()
    private_key = private_key.strip()
    password = password.strip()
    host_fingerprint = host_fingerprint.strip()
    if not host or not user or not (private_key or password):
        return RedirectResponse(
            url="/capabilities?err=" + quote("Host, user, and a key or password are required."),
            status_code=303,
        )
    db = SessionLocal()
    try:
        _upsert_vault(db, "ssh-host", "SSH_HOST", host)
        _upsert_vault(db, "ssh-user", "SSH_USER", user)
        _upsert_vault(db, "ssh-port", "SSH_PORT", (port.strip() or "22"))
        if private_key:
            _upsert_vault(db, "ssh-private-key", "SSH_PRIVATE_KEY", private_key)
        if password:
            _upsert_vault(db, "ssh-password", "SSH_PASSWORD", password)
        if host_fingerprint:
            _upsert_vault(db, "ssh-host-fingerprint", "SSH_HOST_FINGERPRINT", host_fingerprint)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error("Saving SSH config failed: %s", e)
        return RedirectResponse(
            url="/capabilities?err=" + quote("Could not save SSH config."), status_code=303
        )
    finally:
        db.close()
    from src.web.routes_keys import sync_keys_to_env
    sync_keys_to_env()
    return RedirectResponse(
        url="/capabilities?msg=" + quote("SSH bridge configuration saved to vault."),
        status_code=303,
    )
