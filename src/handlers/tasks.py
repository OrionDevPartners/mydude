from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from src.database import SessionLocal
from src.models import Task


async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id

        if not context.args:
            await update.message.reply_text(
                "Usage: /addtask <title>\n"
                "Or: /addtask <title>|<description>|<priority>|<category>\n\n"
                "Priority: low, medium, high\n"
                "Example: /addtask Fix bug|Login page crash|high|backend"
            )
            return

        raw = " ".join(context.args)
        parts = [p.strip() for p in raw.split("|")]

        title = parts[0]
        description = parts[1] if len(parts) > 1 else None
        priority = parts[2] if len(parts) > 2 else "medium"
        category = parts[3] if len(parts) > 3 else None

        session = SessionLocal()
        try:
            task = Task(
                user_id=user_id,
                title=title,
                description=description,
                priority=priority,
                category=category,
            )
            session.add(task)
            session.commit()
            await update.message.reply_text(f"✅ Task #{task.id} added: {title}")
        finally:
            session.close()

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        session = SessionLocal()
        try:
            tasks = (
                session.query(Task)
                .filter_by(user_id=user_id, status="pending")
                .order_by(Task.category, Task.priority, Task.created_at)
                .all()
            )

            if not tasks:
                await update.message.reply_text("📋 No pending tasks.")
                return

            grouped = {}
            for t in tasks:
                cat = t.category or "Uncategorized"
                grouped.setdefault(cat, []).append(t)

            lines = ["📋 Pending Tasks\n"]
            for cat, cat_tasks in grouped.items():
                lines.append(f"\n{cat}")
                for t in cat_tasks:
                    priority_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(t.priority, "⚪")
                    lines.append(f"  {priority_icon} #{t.id} - {t.title}")

            msg = "\n".join(lines)
            if len(msg) > 4000:
                msg = msg[:4000] + "\n... (truncated)"
            await update.message.reply_text(msg)
        finally:
            session.close()

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def done_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id

        if not context.args:
            await update.message.reply_text("Usage: /donetask <id>")
            return

        try:
            task_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Please provide a valid task ID number.")
            return

        session = SessionLocal()
        try:
            task = session.query(Task).filter_by(id=task_id, user_id=user_id).first()
            if not task:
                await update.message.reply_text(f"Task #{task_id} not found.")
                return

            task.status = "completed"
            session.commit()
            await update.message.reply_text(f"✅ Task #{task_id} marked as complete!")
        finally:
            session.close()

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def del_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id

        if not context.args:
            await update.message.reply_text("Usage: /deltask <id>")
            return

        try:
            task_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Please provide a valid task ID number.")
            return

        session = SessionLocal()
        try:
            task = session.query(Task).filter_by(id=task_id, user_id=user_id).first()
            if not task:
                await update.message.reply_text(f"Task #{task_id} not found.")
                return

            session.delete(task)
            session.commit()
            await update.message.reply_text(f"🗑️ Task #{task_id} deleted.")
        finally:
            session.close()

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


def get_handlers():
    return [
        CommandHandler("addtask", add_task),
        CommandHandler("tasks", list_tasks),
        CommandHandler("donetask", done_task),
        CommandHandler("deltask", del_task),
    ]
