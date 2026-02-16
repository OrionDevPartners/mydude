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

async def memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("Authorization required. Use /authorize <password> first.")
            return

        from src.services.memory import search_memory, get_recent_memories, get_memory_by_id, get_memory_stats

        if not context.args:
            stats = await asyncio.to_thread(get_memory_stats, user_id)
            recent = await asyncio.to_thread(get_recent_memories, user_id, None, 5)
            lines = ["CONVERSATION MEMORY", "=" * 40, "", f"Total memories: {stats['total']}", ""]
            if stats.get("by_source"):
                lines.append("By source:")
                for src, count in stats["by_source"].items():
                    lines.append(f"  {src}: {count}")
                lines.append("")
            if recent:
                lines.append("Recent:")
                for m in recent:
                    lines.append(f"  [{m['created_at'][:10]}] #{m['id']} ({m['source']}): {m['summary'][:80]}")
            lines.extend(["", "Usage:", "/memory search <query> - Search memories", "/memory view <id> - View full memory", "/memory recent [source] - Recent memories"])
            output = "\n".join(lines)
            if len(output) > TELEGRAM_MAX:
                output = output[:TELEGRAM_MAX - 50] + "\n\n[Truncated]"
            await update.message.reply_text(output)
            return

        action = context.args[0].lower()

        if action == "search" and len(context.args) > 1:
            query = " ".join(context.args[1:])
            results = await asyncio.to_thread(search_memory, user_id, query)
            if not results:
                await update.message.reply_text(f"No memories matching '{query}'.")
                return
            lines = [f"MEMORY SEARCH: '{query}'", "=" * 40, ""]
            for m in results:
                lines.append(f"#{m['id']} [{m['created_at'][:10]}] ({m['source']})")
                lines.append(f"  {m['summary'][:100]}")
                if m.get('entities'):
                    lines.append(f"  Tags: {m['entities'][:80]}")
                lines.append("")
            output = "\n".join(lines)

        elif action == "view" and len(context.args) > 1:
            try:
                mem_id = int(context.args[1])
            except ValueError:
                await update.message.reply_text("Usage: /memory view <id>")
                return
            mem = await asyncio.to_thread(get_memory_by_id, mem_id)
            if not mem:
                await update.message.reply_text(f"Memory #{mem_id} not found.")
                return
            output = f"MEMORY #{mem['id']}\n{'=' * 40}\nSource: {mem['source']}\nDate: {mem['created_at'][:16]}\n"
            if mem.get('entities'):
                output += f"Entities: {mem['entities']}\n"
            if mem.get('summary'):
                output += f"\nSummary:\n{mem['summary']}\n"
            output += f"\nFull Content:\n{'-' * 30}\n{mem['content']}"

        elif action == "recent":
            source = context.args[1] if len(context.args) > 1 else None
            results = await asyncio.to_thread(get_recent_memories, user_id, source, 15)
            if not results:
                await update.message.reply_text("No recent memories found.")
                return
            title = f"RECENT MEMORIES" + (f" ({source})" if source else "")
            lines = [title, "=" * 40, ""]
            for m in results:
                lines.append(f"#{m['id']} [{m['created_at'][:10]}] ({m['source']}): {m['summary'][:80]}")
            output = "\n".join(lines)
        else:
            output = "Usage:\n/memory - Overview & stats\n/memory search <query> - Search\n/memory view <id> - View full entry\n/memory recent [source] - Recent entries"

        if len(output) > TELEGRAM_MAX:
            output = output[:TELEGRAM_MAX - 50] + "\n\n[Truncated]"
        await update.message.reply_text(output)
    except Exception as e:
        if update.message:
            await update.message.reply_text(f"Error: {str(e)[:500]}")

def get_handlers():
    return [CommandHandler("memory", memory_command)]
