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

async def askcode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("Authorization required.")
            return

        if not context.args:
            await update.message.reply_text(
                "ASK CODE - RAG over your codebase\n" + "=" * 40 + "\n\n"
                "Usage: /askcode <question about the codebase>\n\n"
                "Examples:\n"
                "  /askcode how does authentication work\n"
                "  /askcode where is the database connection configured\n"
                "  /askcode what does the broker do\n\n"
                "Also:\n"
                "  /codestructure - Show project file tree"
            )
            return

        question = " ".join(context.args)
        await update.message.reply_text(f"Searching codebase for: {question}...")

        from src.services.rag import build_rag_context
        rag_context = await build_rag_context(question)

        llm = context.bot_data.get("llm_instance")
        if not llm:
            output = f"CODE SEARCH RESULTS\n{'=' * 40}\n\n{rag_context[:3500]}"
            if len(output) > TELEGRAM_MAX:
                output = output[:TELEGRAM_MAX - 50] + "\n\n[Truncated]"
            await update.message.reply_text(output)
            return

        result = await llm.call_team(
            "You are a codebase expert. Answer the user's question about the codebase using ONLY the provided code context. Be specific, reference file names and line numbers when relevant. If the context doesn't contain enough info, say so.",
            f"QUESTION: {question}\n\nCODE CONTEXT:\n{rag_context}",
            roles_hint={"openai": "code analyst", "anthropic": "architecture expert", "gemini": "documentation writer", "grok": "pattern finder"}
        )

        merged = result.get("merged", "")
        from src.services.memory import store_memory
        from src.services.audit import log_command
        await asyncio.to_thread(store_memory, user_id, "askcode", merged, merged[:200], question)
        await asyncio.to_thread(log_command, user_id, "askcode", question, "ok", merged[:200])

        output = f"CODE ANSWER\n{'=' * 40}\nQ: {question}\n\n{merged}"
        if len(output) > TELEGRAM_MAX:
            output = output[:TELEGRAM_MAX - 50] + "\n\n[Truncated]"
        await update.message.reply_text(output)
    except Exception as e:
        if update.message:
            await update.message.reply_text(f"Error: {str(e)[:500]}")

async def codestructure_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        if not is_authorized(update.effective_user.id):
            await update.message.reply_text("Authorization required.")
            return

        from src.services.rag import get_project_structure
        structure = await get_project_structure()
        output = f"PROJECT STRUCTURE\n{'=' * 40}\n\n{structure}"
        if len(output) > TELEGRAM_MAX:
            output = output[:TELEGRAM_MAX - 50] + "\n\n[Truncated]"
        await update.message.reply_text(output)
    except Exception as e:
        if update.message:
            await update.message.reply_text(f"Error: {str(e)[:500]}")

def get_handlers():
    return [
        CommandHandler("askcode", askcode_command),
        CommandHandler("codestructure", codestructure_command),
    ]
