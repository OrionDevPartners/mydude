import os
import logging
import secrets

logger = logging.getLogger(__name__)

def get_webhook_config() -> dict:
    """Get webhook configuration from environment."""
    mode = os.getenv("BOT_MODE", "polling").lower()
    webhook_url = os.getenv("WEBHOOK_URL", "")
    webhook_secret = os.getenv("WEBHOOK_SECRET", "")
    
    if not webhook_secret and mode == "webhook":
        webhook_secret = secrets.token_hex(32)
        os.environ["WEBHOOK_SECRET"] = webhook_secret
    
    if not webhook_url and mode == "webhook":
        repl_slug = os.getenv("REPL_SLUG", "")
        repl_owner = os.getenv("REPL_OWNER", "")
        if repl_slug and repl_owner:
            webhook_url = f"https://{repl_slug}.{repl_owner}.repl.co/webhook"
    
    return {
        "mode": mode,
        "webhook_url": webhook_url,
        "webhook_secret": webhook_secret,
        "port": 5000,
        "listen": "0.0.0.0",
    }

def is_webhook_mode() -> bool:
    return os.getenv("BOT_MODE", "polling").lower() == "webhook"
