import os
import time
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from src.database import SessionLocal
from src.models import UserSettings

_failed_attempts = {}

RATE_LIMIT_MAX = 3
RATE_LIMIT_WINDOW = 300


def _check_rate_limit(user_id):
    now = time.time()
    if user_id in _failed_attempts:
        attempts = _failed_attempts[user_id]
        attempts = [t for t in attempts if now - t < RATE_LIMIT_WINDOW]
        _failed_attempts[user_id] = attempts
        if len(attempts) >= RATE_LIMIT_MAX:
            return False
    return True


def _record_failed_attempt(user_id):
    now = time.time()
    if user_id not in _failed_attempts:
        _failed_attempts[user_id] = []
    _failed_attempts[user_id].append(now)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        welcome = (
            "👋 *Welcome to Replit Manager Bot!*\n\n"
            "I help you manage your Replit project with:\n"
            "📋 *Tasks* - Track your to-dos\n"
            "📝 *Notes* - Save quick notes\n"
            "🖥️ *Shell* - Run commands on the Replit server\n"
            "🔀 *Git* - Manage your repository\n"
            "🤖 *Swarm* - Porter waves multi-agent orchestration\n\n"
            "Use /help to see all available commands.\n"
            "Use /authorize <password> to unlock shell and git commands."
        )
        await update.message.reply_text(welcome, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        help_text = (
            "📖 *Available Commands*\n\n"
            "*General*\n"
            "/start - Welcome message\n"
            "/help - Show this help\n"
            "/authorize <password> - Authorize for admin commands\n"
            "/whoami - Show your user info\n\n"
            "*Tasks*\n"
            "/addtask <title> - Add a task\n"
            "  Format: title|description|priority|category\n"
            "/tasks - List pending tasks\n"
            "/donetask <id> - Mark task complete\n"
            "/deltask <id> - Delete a task\n\n"
            "*Notes*\n"
            "/addnote <title> | <content> | <category>\n"
            "/notes - List all notes\n"
            "/viewnote <id> - View full note\n"
            "/delnote <id> - Delete a note\n\n"
            "*Shell (authorized only)*\n"
            "/shell <command> - Run on Replit server\n\n"
            "*Git (authorized only)*\n"
            "/gitstatus - Git status\n"
            "/gitlog - Recent commits\n"
            "/gitdiff - Show diff\n"
            "/gitcommit <msg> - Add all & commit\n"
            "/gitpull - Pull changes\n"
            "/gitpush - Push changes\n\n"
            "*Swarm (authorized only)*\n"
            "/goal <objective> - Launch Porter swarm\n"
            "/waves - Swarm status & config\n"
            "/policy - View/toggle production policy"
        )
        await update.message.reply_text(help_text, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def authorize_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        admin_password = os.environ.get("ADMIN_PASSWORD")

        if not admin_password:
            await update.message.reply_text("⚠️ ADMIN_PASSWORD not configured on server.")
            return

        if not context.args:
            await update.message.reply_text("Usage: /authorize <password>")
            return

        if not _check_rate_limit(user_id):
            await update.message.reply_text("🚫 Too many failed attempts. Please try again later.")
            return

        admin_user_id = os.environ.get("ADMIN_USER_ID")
        if admin_user_id:
            try:
                if user_id != int(admin_user_id):
                    _record_failed_attempt(user_id)
                    await update.message.reply_text("❌ Authorization denied.")
                    return
            except ValueError:
                await update.message.reply_text("⚠️ ADMIN_USER_ID is misconfigured on server.")
                return

        password = " ".join(context.args)
        if password != admin_password:
            _record_failed_attempt(user_id)
            await update.message.reply_text("❌ Wrong password.")
            return

        session = SessionLocal()
        try:
            settings = session.query(UserSettings).filter_by(user_id=user_id).first()
            if not settings:
                settings = UserSettings(user_id=user_id, authorized=True)
                session.add(settings)
            else:
                settings.authorized = True
            session.commit()
            await update.message.reply_text("✅ You are now authorized! Shell and Git commands are unlocked.")
        finally:
            session.close()

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def whoami_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        user_id = user.id

        session = SessionLocal()
        try:
            settings = session.query(UserSettings).filter_by(user_id=user_id).first()
            authorized = settings.authorized if settings else False
            timezone = settings.timezone if settings else "UTC"
        finally:
            session.close()

        auth_status = "✅ Authorized" if authorized else "❌ Not authorized"
        username = f"@{user.username}" if user.username else "N/A"
        info = (
            f"👤 User Info\n\n"
            f"Name: {user.full_name}\n"
            f"Username: {username}\n"
            f"User ID: {user_id}\n"
            f"Status: {auth_status}\n"
            f"Timezone: {timezone}"
        )
        await update.message.reply_text(info)

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


def get_handlers():
    return [
        CommandHandler("start", start_command),
        CommandHandler("help", help_command),
        CommandHandler("authorize", authorize_command),
        CommandHandler("whoami", whoami_command),
    ]
