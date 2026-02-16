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

async def audit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("Authorization required. Use /authorize <password> first.")
            return

        from src.services.audit import get_audit_log, search_audit
        
        if context.args and context.args[0].lower() == "search" and len(context.args) > 1:
            query = " ".join(context.args[1:])
            entries = await asyncio.to_thread(search_audit, query)
            if not entries:
                await update.message.reply_text(f"No audit entries matching '{query}'.")
                return
            lines = [f"AUDIT SEARCH: '{query}'", "=" * 40, ""]
            for e in entries:
                lines.append(f"[{e['created_at'][:16]}] /{e['command']} {e['args'][:60]} ({e['status']})")
            output = "\n".join(lines)
        else:
            limit = 20
            if context.args:
                try:
                    limit = min(int(context.args[0]), 50)
                except ValueError:
                    pass
            entries = await asyncio.to_thread(get_audit_log, user_id, limit)
            if not entries:
                await update.message.reply_text("No audit log entries yet.")
                return
            lines = [f"AUDIT LOG (last {len(entries)} commands)", "=" * 40, ""]
            for e in entries:
                lines.append(f"[{e['created_at'][:16]}] /{e['command']} ({e['status']})")
                if e.get('args'):
                    lines.append(f"  Args: {e['args'][:80]}")
            output = "\n".join(lines)
        
        if len(output) > TELEGRAM_MAX:
            output = output[:TELEGRAM_MAX - 50] + "\n\n[Truncated]"
        await update.message.reply_text(output)
    except Exception as e:
        if update.message:
            await update.message.reply_text(f"Error: {str(e)[:500]}")

def get_handlers():
    return [CommandHandler("audit", audit_command)]
