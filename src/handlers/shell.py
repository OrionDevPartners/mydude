import shlex
import subprocess
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from src.database import SessionLocal
from src.models import CommandLog, UserSettings


def is_authorized(user_id):
    session = SessionLocal()
    try:
        settings = session.query(UserSettings).filter_by(user_id=user_id).first()
        return settings and settings.authorized
    finally:
        session.close()


async def shell_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id

        if not is_authorized(user_id):
            await update.message.reply_text("⛔ You are not authorized to use shell commands. Use /authorize <password> first.")
            return

        if not context.args:
            await update.message.reply_text("Usage: /shell <command>\n\nThis runs commands on the Replit server, not on your local machine.")
            return

        cmd = " ".join(context.args)
        await update.message.reply_text(f"⏳ Running: {cmd}")

        try:
            result = subprocess.run(
                shlex.split(cmd), capture_output=True, text=True, timeout=30
            )
            output = ""
            if result.stdout:
                output += result.stdout
            if result.stderr:
                output += "\n--- STDERR ---\n" + result.stderr
            if not output.strip():
                output = "(no output)"
            status = "success" if result.returncode == 0 else "error"
        except subprocess.TimeoutExpired:
            output = "⏰ Command timed out after 30 seconds."
            status = "timeout"
        except Exception as e:
            output = f"Error: {str(e)}"
            status = "error"

        session = SessionLocal()
        try:
            log = CommandLog(user_id=user_id, command=cmd, output=output, status=status)
            session.add(log)
            session.commit()
        finally:
            session.close()

        if len(output) > 4000:
            output = output[:4000] + "\n... (truncated)"

        await update.message.reply_text(output)

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


def get_handlers():
    return [CommandHandler("shell", shell_command)]
