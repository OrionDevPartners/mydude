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

async def goals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("Authorization required.")
            return

        from src.services.goals import get_all_goals
        goals = await asyncio.to_thread(get_all_goals, user_id)
        if not goals:
            await update.message.reply_text("No goals found. Use /goal <objective> to create one.")
            return

        lines = ["ALL GOALS", "=" * 40, ""]
        for g in goals:
            status_icon = {"active": "[ACTIVE]", "completed": "[DONE]", "cancelled": "[CANCELLED]"}.get(g["status"], f"[{g['status']}]")
            lines.append(f"#{g['id']} {status_icon} {g['objective'][:60]}")
            lines.append(f"  Progress: {g['progress_pct']}% | Waves: {g['wave_count']} | {g['created_at'][:10]}")
            lines.append("")

        output = "\n".join(lines)
        if len(output) > TELEGRAM_MAX:
            output = output[:TELEGRAM_MAX - 50] + "\n\n[Truncated]"
        await update.message.reply_text(output)
    except Exception as e:
        if update.message:
            await update.message.reply_text(f"Error: {str(e)[:500]}")

async def goalstatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("Authorization required.")
            return

        if not context.args:
            await update.message.reply_text("Usage: /goalstatus <goal_id>")
            return

        from src.services.goals import get_goal_by_id
        try:
            goal_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Goal ID must be a number.")
            return

        goal = await asyncio.to_thread(get_goal_by_id, goal_id)
        if not goal:
            await update.message.reply_text(f"Goal #{goal_id} not found.")
            return

        output = (
            f"GOAL #{goal['id']}\n{'=' * 40}\n"
            f"Objective: {goal['objective']}\n"
            f"Status: {goal['status']}\n"
            f"Progress: {goal['progress_pct']}%\n"
            f"Waves run: {goal['wave_count']}\n"
            f"Created: {goal['created_at'][:16]}\n"
            f"Updated: {goal['updated_at'][:16]}\n"
        )
        if goal.get("last_result"):
            output += f"\nLast Result:\n{'-' * 30}\n{goal['last_result'][:2000]}"

        if len(output) > TELEGRAM_MAX:
            output = output[:TELEGRAM_MAX - 50] + "\n\n[Truncated]"
        await update.message.reply_text(output)
    except Exception as e:
        if update.message:
            await update.message.reply_text(f"Error: {str(e)[:500]}")

async def goalcomplete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("Authorization required.")
            return

        if not context.args:
            await update.message.reply_text("Usage: /goalcomplete <goal_id>")
            return

        from src.services.goals import complete_goal
        try:
            goal_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Goal ID must be a number.")
            return

        result = await asyncio.to_thread(complete_goal, goal_id)
        if result:
            await update.message.reply_text(f"Goal #{goal_id} marked as completed.")
        else:
            await update.message.reply_text(f"Goal #{goal_id} not found.")
    except Exception as e:
        if update.message:
            await update.message.reply_text(f"Error: {str(e)[:500]}")

def get_handlers():
    return [
        CommandHandler("goals", goals_command),
        CommandHandler("goalstatus", goalstatus_command),
        CommandHandler("goalcomplete", goalcomplete_command),
    ]
