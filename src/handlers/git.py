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


def run_git_command(cmd):
    try:
        if isinstance(cmd, str):
            cmd = shlex.split(cmd)
        result = subprocess.run(
            cmd, shell=False, capture_output=True, text=True, timeout=30
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += "\n" + result.stderr
        if not output.strip():
            output = "(no output)"
        status = "success" if result.returncode == 0 else "error"
        return output.strip(), status
    except subprocess.TimeoutExpired:
        return "⏰ Command timed out after 30 seconds.", "timeout"
    except Exception as e:
        return f"Error: {str(e)}", "error"


def log_command(user_id, command, output, status):
    session = SessionLocal()
    try:
        log = CommandLog(user_id=user_id, command=command, output=output, status=status)
        session.add(log)
        session.commit()
    finally:
        session.close()


async def git_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("⛔ Not authorized. Use /authorize <password> first.")
            return

        output, status = run_git_command("git status")
        log_command(user_id, "git status", output, status)

        if len(output) > 4000:
            output = output[:4000] + "\n... (truncated)"
        await update.message.reply_text(output)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def git_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("⛔ Not authorized. Use /authorize <password> first.")
            return

        output, status = run_git_command("git log --oneline -10")
        log_command(user_id, "git log --oneline -10", output, status)

        if len(output) > 4000:
            output = output[:4000] + "\n... (truncated)"
        await update.message.reply_text(output)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def git_diff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("⛔ Not authorized. Use /authorize <password> first.")
            return

        output, status = run_git_command("git diff")
        log_command(user_id, "git diff", output, status)

        if len(output) > 4000:
            output = output[:4000] + "\n... (truncated)"
        await update.message.reply_text(output)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def git_commit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("⛔ Not authorized. Use /authorize <password> first.")
            return

        if not context.args:
            await update.message.reply_text("Usage: /gitcommit <message>")
            return

        message = " ".join(context.args)
        add_output, add_status = run_git_command(["git", "add", "-A"])
        if add_status != "success":
            log_command(user_id, "git add -A", add_output, add_status)
            await update.message.reply_text(add_output)
            return
        cmd = ["git", "commit", "-m", message]
        output, status = run_git_command(cmd)
        log_command(user_id, "git add -A && git commit", output, status)

        if len(output) > 4000:
            output = output[:4000] + "\n... (truncated)"
        await update.message.reply_text(output)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def git_pull(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("⛔ Not authorized. Use /authorize <password> first.")
            return

        output, status = run_git_command("git pull")
        log_command(user_id, "git pull", output, status)

        if len(output) > 4000:
            output = output[:4000] + "\n... (truncated)"
        await update.message.reply_text(output)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def git_push(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("⛔ Not authorized. Use /authorize <password> first.")
            return

        output, status = run_git_command("git push")
        log_command(user_id, "git push", output, status)

        if len(output) > 4000:
            output = output[:4000] + "\n... (truncated)"
        await update.message.reply_text(output)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


def get_handlers():
    return [
        CommandHandler("gitstatus", git_status),
        CommandHandler("gitlog", git_log),
        CommandHandler("gitdiff", git_diff),
        CommandHandler("gitcommit", git_commit),
        CommandHandler("gitpull", git_pull),
        CommandHandler("gitpush", git_push),
    ]
