import asyncio
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes
from src.database import SessionLocal
from src.models import UserSettings

TELEGRAM_MAX = 4000

def is_authorized(user_id: int) -> bool:
    session = SessionLocal()
    try:
        settings = session.query(UserSettings).filter(UserSettings.user_id == user_id).first()
        return bool(settings and settings.authorized)
    finally:
        session.close()

async def digest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("Authorization required.")
            return

        from src.services.digest import get_digest_config, set_digest_config, toggle_digest, build_digest

        if not context.args:
            config = await asyncio.to_thread(get_digest_config, user_id)
            if config:
                lines = [
                    "DIGEST SETTINGS", "=" * 40, "",
                    f"Status: {'ENABLED' if config['enabled'] else 'DISABLED'}",
                    f"Frequency: {config['frequency']}",
                    f"Send at: {config['hour_utc']}:00 UTC",
                    f"Last sent: {config['last_sent']}",
                ]
            else:
                lines = ["DIGEST SETTINGS", "=" * 40, "", "Not configured yet."]
            lines.extend(["", "Usage:", "/digest now - Send digest now", "/digest daily [hour] - Set daily digest (hour in UTC, default 9)", "/digest weekly [hour] [day] - Set weekly (day: 0=Mon..6=Sun)", "/digest toggle - Enable/disable"])
            await update.message.reply_text("\n".join(lines))
            return

        action = context.args[0].lower()

        if action == "now":
            digest_text = await asyncio.to_thread(build_digest, user_id)
            if len(digest_text) > TELEGRAM_MAX:
                digest_text = digest_text[:TELEGRAM_MAX - 50] + "\n\n[Truncated]"
            await update.message.reply_text(digest_text)

        elif action == "daily":
            hour = 9
            if len(context.args) > 1:
                try:
                    hour = int(context.args[1])
                    hour = max(0, min(23, hour))
                except ValueError:
                    pass
            await asyncio.to_thread(set_digest_config, user_id, "daily", hour)
            await update.message.reply_text(f"Daily digest configured at {hour}:00 UTC.")

        elif action == "weekly":
            hour = 9
            day = 0
            if len(context.args) > 1:
                try:
                    hour = max(0, min(23, int(context.args[1])))
                except ValueError:
                    pass
            if len(context.args) > 2:
                try:
                    day = max(0, min(6, int(context.args[2])))
                except ValueError:
                    pass
            await asyncio.to_thread(set_digest_config, user_id, "weekly", hour)
            await update.message.reply_text(f"Weekly digest configured: day {day} at {hour}:00 UTC.")

        elif action == "toggle":
            result = await asyncio.to_thread(toggle_digest, user_id)
            status = "enabled" if result else "disabled"
            await update.message.reply_text(f"Digest {status}.")
        else:
            await update.message.reply_text("Unknown action. Use: now, daily, weekly, toggle")
    except Exception as e:
        if update.message:
            await update.message.reply_text(f"Error: {str(e)[:500]}")

def get_handlers():
    return [CommandHandler("digest", digest_command)]
