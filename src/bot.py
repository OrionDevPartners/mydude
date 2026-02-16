import os
import logging
from telegram.ext import ApplicationBuilder
from src.database import init_db
from src.handlers import help, shell, tasks, notes, git, swarm

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


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
    app = ApplicationBuilder().token(token).build()

    for module in [help, shell, tasks, notes, git, swarm]:
        for handler in module.get_handlers():
            app.add_handler(handler)

    logger.info("Starting bot polling...")
    app.run_polling(drop_pending_updates=True)
