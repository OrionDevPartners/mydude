import os
import logging
from telegram.ext import ApplicationBuilder
from src.database import init_db
from src.handlers import help, shell, tasks, notes, git, swarm, extract, voice, ingest
from src.handlers import rag, triage
from src.handlers import audit as audit_handler
from src.handlers import memory as memory_handler
from src.handlers import goals as goals_handler
from src.handlers import cron_handler
from src.handlers import digest as digest_handler
from src.handlers import integrations as integrations_handlers
from src.handlers import selfheal as selfheal_handlers
from src.handlers import cognition as cognition_handler
from src.selfheal import HealthMonitor

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_circuit_breaker = None

try:
    from src.swarm.llm_multi import MultiProviderLLM
    _llm_instance = MultiProviderLLM()
    _circuit_breaker = _llm_instance.circuit_breaker
except Exception:
    logger.warning("Could not initialize MultiProviderLLM for circuit breaker")
    _llm_instance = None


async def _post_init(app):
    admin_chat_id = os.environ.get("ADMIN_USER_ID")

    async def _alert_callback(msg: str):
        if admin_chat_id:
            try:
                await app.bot.send_message(chat_id=int(admin_chat_id), text=msg)
            except Exception:
                logger.exception("Failed to send health alert to admin")

    health_monitor = HealthMonitor(
        circuit_breaker=app.bot_data.get("circuit_breaker"),
        alert_callback=_alert_callback if admin_chat_id else None,
    )
    app.bot_data["health_monitor"] = health_monitor
    await health_monitor.start(interval=120)
    logger.info("HealthMonitor background task started.")

    try:
        from src.services.cron import CronRunner
        cron_runner = CronRunner(bot_app=app)
        app.bot_data["cron_runner"] = cron_runner
        await cron_runner.start(check_interval=60)
        logger.info("CronRunner background task started.")
    except Exception:
        logger.warning("Failed to start CronRunner")

    try:
        from src.services.digest import DigestRunner
        digest_runner = DigestRunner(bot_app=app)
        app.bot_data["digest_runner"] = digest_runner
        await digest_runner.start()
        logger.info("DigestRunner background task started.")
    except Exception:
        logger.warning("Failed to start DigestRunner")


def run_bot():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN environment variable is not set. "
            "Please set it to your Telegram bot token from @BotFather."
        )

    logger.info("Initializing database...")
    init_db()
    logger.info("Database initialized.")

    logger.info("Building bot application...")
    app = ApplicationBuilder().token(token).post_init(_post_init).build()

    if _circuit_breaker:
        app.bot_data["circuit_breaker"] = _circuit_breaker

    if _llm_instance:
        app.bot_data["llm_instance"] = _llm_instance

    all_handler_modules = [
        help, shell, tasks, notes, git, swarm, selfheal_handlers, extract,
        voice, ingest, rag, triage,
        audit_handler, memory_handler, goals_handler, cron_handler, digest_handler,
        integrations_handlers, cognition_handler,
    ]
    for module in all_handler_modules:
        for handler in module.get_handlers():
            app.add_handler(handler)

    mode = os.environ.get("BOT_MODE", "polling").lower()
    
    if mode == "webhook":
        from src.services.webhooks import get_webhook_config
        config = get_webhook_config()
        logger.info(f"Starting bot in WEBHOOK mode on port {config['port']}...")
        app.run_webhook(
            listen=config["listen"],
            port=config["port"],
            url_path="/webhook",
            webhook_url=config["webhook_url"] + "/webhook" if not config["webhook_url"].endswith("/webhook") else config["webhook_url"],
            secret_token=config["webhook_secret"],
            drop_pending_updates=True,
        )
    else:
        logger.info("Starting bot polling...")
        app.run_polling(drop_pending_updates=True)
