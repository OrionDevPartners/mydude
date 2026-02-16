import asyncio
from telegram import Update
from telegram.ext import CommandHandler, MessageHandler, ContextTypes, filters
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

async def triage_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("Authorization required.")
            return

        if not context.args:
            await update.message.reply_text(
                "AUTO-TRIAGE\n" + "=" * 40 + "\n\n"
                "Usage: /triage <message text to classify>\n\n"
                "Or forward a message to me and I'll classify it.\n\n"
                "Categories: URGENT, ACTION, FYI, QUESTION, DECISION\n"
                "Priorities: HIGH, MEDIUM, LOW"
            )
            return

        text = " ".join(context.args)
        await update.message.reply_text("Analyzing message...")

        llm = context.bot_data.get("llm_instance")
        if llm:
            from src.services.triage import ai_triage
            result = await ai_triage(text, llm)
        else:
            from src.services.triage import classify_message
            result = classify_message(text)

        from src.services.audit import log_command
        await asyncio.to_thread(log_command, user_id, "triage", text[:200], "ok", str(result))

        output = (
            f"TRIAGE RESULT\n{'=' * 40}\n\n"
            f"Category: {result['category'].upper()}\n"
            f"Priority: {result['priority'].upper()}\n"
        )
        if result.get("summary"):
            output += f"Summary: {result['summary']}\n"
        if result.get("confidence"):
            output += f"Confidence: {result['confidence']}\n"
        if result.get("ai_analyzed"):
            output += "Method: AI-powered (4-provider swarm)\n"
        else:
            output += "Method: Keyword-based\n"

        await update.message.reply_text(output)
    except Exception as e:
        if update.message:
            await update.message.reply_text(f"Error: {str(e)[:500]}")

async def forwarded_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto-triage forwarded messages."""
    try:
        if not update.message or not update.effective_user:
            return
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            return

        if not update.message.forward_date:
            return

        text = update.message.text or update.message.caption or ""
        if not text or len(text) < 10:
            return

        from src.services.triage import classify_message
        result = classify_message(text)

        from src.services.memory import store_memory
        await asyncio.to_thread(store_memory, user_id, "forwarded", text[:2000], f"{result['category']}: {text[:100]}", result['category'])

        if result["priority"] == "high" or result["category"] == "urgent":
            output = (
                f"FORWARDED MESSAGE TRIAGE\n{'=' * 30}\n"
                f"Category: {result['category'].upper()}\n"
                f"Priority: {result['priority'].upper()}\n"
                f"Confidence: {result.get('confidence', '?')}\n\n"
                f"This looks important! Use /triage to get AI-powered analysis."
            )
            await update.message.reply_text(output)
    except Exception:
        pass

async def metrics_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show provider performance metrics."""
    try:
        if not update.message or not update.effective_user:
            return
        if not is_authorized(update.effective_user.id):
            await update.message.reply_text("Authorization required.")
            return

        from src.services.metrics import get_provider_stats, get_provider_weights

        days = 7
        if context.args:
            try:
                days = min(int(context.args[0]), 90)
            except ValueError:
                pass

        stats = await asyncio.to_thread(get_provider_stats, days)
        weights = await asyncio.to_thread(get_provider_weights, days)

        if not stats:
            await update.message.reply_text("No provider metrics recorded yet. Use /extract, /goal, or /askcode to generate data.")
            return

        lines = [f"PROVIDER METRICS (last {days} days)", "=" * 40, ""]
        for provider, s in sorted(stats.items()):
            lines.append(f"{provider.upper()}")
            lines.append(f"  Calls: {s['total_calls']}")
            lines.append(f"  Success rate: {s['success_rate']}%")
            lines.append(f"  Avg latency: {s['avg_latency_ms']}ms")
            if s.get('avg_rating'):
                lines.append(f"  Avg quality: {s['avg_rating']}")
            lines.append(f"  Weight: {weights.get(provider, '?')}")
            lines.append("")

        output = "\n".join(lines)
        if len(output) > TELEGRAM_MAX:
            output = output[:TELEGRAM_MAX - 50] + "\n\n[Truncated]"
        await update.message.reply_text(output)
    except Exception as e:
        if update.message:
            await update.message.reply_text(f"Error: {str(e)[:500]}")

def get_handlers():
    return [
        CommandHandler("triage", triage_command),
        CommandHandler("metrics", metrics_command),
        MessageHandler(filters.FORWARDED & filters.TEXT, forwarded_handler),
    ]
