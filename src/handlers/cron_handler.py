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

async def cron_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("Authorization required.")
            return

        from src.services.cron import add_cron_job, get_user_jobs, toggle_job, delete_job

        if not context.args:
            jobs = await asyncio.to_thread(get_user_jobs, user_id)
            lines = ["SCHEDULED JOBS", "=" * 40, ""]
            if jobs:
                for j in jobs:
                    status = "ON" if j["enabled"] else "OFF"
                    lines.append(f"#{j['id']} [{status}] every {j['schedule']}")
                    lines.append(f"  Command: {j['command'][:60]}")
                    lines.append(f"  Last: {j['last_run']} | Next: {j['next_run'][:16] if j['next_run'] != 'unknown' else 'unknown'}")
                    lines.append("")
            else:
                lines.append("No scheduled jobs.")
            lines.extend(["", "Usage:", "/cron add <interval> <command> - Add job (e.g. /cron add 1h ls -la)", "/cron toggle <id> - Enable/disable", "/cron delete <id> - Remove job", "/cron run <id> - Run now", "", "Intervals: 5m, 15m, 1h, 6h, 12h, 24h, 1d, 7d"])
            output = "\n".join(lines)
            if len(output) > TELEGRAM_MAX:
                output = output[:TELEGRAM_MAX - 50] + "\n\n[Truncated]"
            await update.message.reply_text(output)
            return

        action = context.args[0].lower()

        if action == "add" and len(context.args) >= 3:
            schedule = context.args[1]
            command = " ".join(context.args[2:])
            if not any(schedule.endswith(s) for s in ['m', 'h', 'd']):
                await update.message.reply_text("Invalid interval. Use format like: 5m, 1h, 24h, 1d")
                return
            job_id = await asyncio.to_thread(add_cron_job, user_id, schedule, command)
            await update.message.reply_text(f"Cron job #{job_id} created.\nSchedule: every {schedule}\nCommand: {command}")

        elif action == "toggle" and len(context.args) >= 2:
            try:
                job_id = int(context.args[1])
            except ValueError:
                await update.message.reply_text("Job ID must be a number.")
                return
            result = await asyncio.to_thread(toggle_job, job_id, user_id)
            await update.message.reply_text(result)

        elif action == "delete" and len(context.args) >= 2:
            try:
                job_id = int(context.args[1])
            except ValueError:
                await update.message.reply_text("Job ID must be a number.")
                return
            result = await asyncio.to_thread(delete_job, job_id, user_id)
            await update.message.reply_text(result)

        elif action == "run" and len(context.args) >= 2:
            from src.services.cron import execute_job, get_user_jobs, mark_job_run
            try:
                job_id = int(context.args[1])
            except ValueError:
                await update.message.reply_text("Job ID must be a number.")
                return
            jobs = await asyncio.to_thread(get_user_jobs, user_id)
            job = next((j for j in jobs if j["id"] == job_id), None)
            if not job:
                await update.message.reply_text(f"Job #{job_id} not found.")
                return
            await update.message.reply_text(f"Running job #{job_id}...")
            output = await execute_job(job["command"])
            await asyncio.to_thread(mark_job_run, job_id, output)
            result_text = f"JOB #{job_id} OUTPUT\n{'=' * 30}\n{output[:3000]}"
            if len(result_text) > TELEGRAM_MAX:
                result_text = result_text[:TELEGRAM_MAX - 50] + "\n\n[Truncated]"
            await update.message.reply_text(result_text)
        else:
            await update.message.reply_text("Usage: /cron add|toggle|delete|run <args>")
    except Exception as e:
        if update.message:
            await update.message.reply_text(f"Error: {str(e)[:500]}")

def get_handlers():
    return [CommandHandler("cron", cron_command)]
