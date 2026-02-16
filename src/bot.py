import os
import logging
from telegram.ext import ApplicationBuilder
from src.database import init_db
from src.handlers import help, shell, tasks, notes, git, swarm
from src.handlers import selfheal as selfheal_handlers
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

    for module in [help, shell, tasks, notes, git, swarm, selfheal_handlers]:
        for handler in module.get_handlers():
            app.add_handler(handler)

    logger.info("Starting bot polling...")
    app.run_polling(drop_pending_updates=True)
