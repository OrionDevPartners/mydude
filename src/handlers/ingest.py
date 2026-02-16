import os
import asyncio
import logging
import tempfile
from telegram import Update
from telegram.ext import CommandHandler, MessageHandler, ContextTypes, filters
from src.database import SessionLocal
from src.models import UserSettings

logger = logging.getLogger(__name__)
TELEGRAM_MAX = 4000


def is_authorized(user_id: int) -> bool:
    session = SessionLocal()
    try:
        settings = session.query(UserSettings).filter(UserSettings.user_id == user_id).first()
        return bool(settings and settings.authorized)
    finally:
        session.close()


async def ingest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /ingest <url> - fetch and analyze a URL."""
    try:
        if not update.message or not update.effective_user:
            return
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("Authorization required.")
            return

        if not context.args:
            await update.message.reply_text(
                "DOCUMENT INGESTION\n" + "=" * 40 + "\n\n"
                "Usage:\n"
                "/ingest <url> - Fetch and analyze a webpage\n\n"
                "You can also send documents (PDF, TXT, etc.) directly as file attachments.\n"
                "Add 'analyze' in the caption to run swarm analysis."
            )
            return

        url = context.args[0]
        await update.message.reply_text(f"Fetching content from URL...")

        from src.services.ingestion import ingest_url
        content = await ingest_url(url)

        from src.services.memory import store_memory
        from src.services.audit import log_command
        await asyncio.to_thread(store_memory, user_id, "url_ingest", content, content[:200], url)
        await asyncio.to_thread(log_command, user_id, "ingest", url, "ok", content[:200])

        output = f"URL CONTENT EXTRACTED\n{'=' * 40}\nSource: {url}\nLength: {len(content)} chars\n\n{content[:3000]}"

        if len(context.args) > 1 and "analyze" in " ".join(context.args[1:]).lower():
            llm = context.bot_data.get("llm_instance")
            if llm:
                await update.message.reply_text("Running swarm analysis...")
                try:
                    result = await llm.call_team(
                        "You are an expert analyst. Summarize and extract key insights from this content.",
                        f"CONTENT:\n{content[:6000]}",
                        roles_hint={"openai": "summarizer", "anthropic": "key points", "gemini": "insights", "grok": "opportunities"}
                    )
                    merged = result.get("merged", "")
                    await asyncio.to_thread(store_memory, user_id, "url_analysis", merged, merged[:200], url)
                    output += f"\n\nSWARM ANALYSIS\n{'=' * 40}\n\n{merged}"
                except Exception as e:
                    output += f"\n\nAnalysis failed: {str(e)[:200]}"

        if len(output) > TELEGRAM_MAX:
            output = output[:TELEGRAM_MAX - 50] + "\n\n[Truncated]"
        await update.message.reply_text(output)
    except Exception as e:
        logger.exception("Ingest error")
        if update.message:
            await update.message.reply_text(f"Error: {str(e)[:500]}")


async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle document file attachments."""
    try:
        if not update.message or not update.effective_user:
            return
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("Authorization required to ingest documents.")
            return

        doc = update.message.document
        if not doc:
            return

        file_name = doc.file_name or "unknown"
        file_size = doc.file_size or 0

        if file_size > 10 * 1024 * 1024:
            await update.message.reply_text("File too large (max 10MB).")
            return

        await update.message.reply_text(f"Processing document: {file_name}...")

        file = await context.bot.get_file(doc.file_id)
        ext = os.path.splitext(file_name)[1] or ".txt"

        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp_path = tmp.name

        try:
            await file.download_to_drive(tmp_path)

            from src.services.ingestion import ingest_file
            content = await ingest_file(tmp_path)

            from src.services.memory import store_memory
            from src.services.audit import log_command
            await asyncio.to_thread(store_memory, user_id, "file_ingest", content, content[:200], file_name)
            await asyncio.to_thread(log_command, user_id, "document", file_name, "ok", content[:200])

            output = f"DOCUMENT INGESTED\n{'=' * 40}\nFile: {file_name}\nSize: {file_size} bytes\nExtracted: {len(content)} chars\n\n{content[:3000]}"

            caption = (update.message.caption or "").lower()
            if "analyze" in caption:
                llm = context.bot_data.get("llm_instance")
                if llm:
                    await update.message.reply_text("Running swarm analysis...")
                    try:
                        result = await llm.call_team(
                            "You are an expert analyst. Summarize and extract key insights from this document.",
                            f"DOCUMENT ({file_name}):\n{content[:6000]}",
                            roles_hint={"openai": "summarizer", "anthropic": "key points", "gemini": "insights", "grok": "opportunities"}
                        )
                        merged = result.get("merged", "")
                        await asyncio.to_thread(store_memory, user_id, "doc_analysis", merged, merged[:200], file_name)
                        output += f"\n\nSWARM ANALYSIS\n{'=' * 40}\n\n{merged}"
                    except Exception as e:
                        output += f"\n\nAnalysis failed: {str(e)[:200]}"

            if len(output) > TELEGRAM_MAX:
                output = output[:TELEGRAM_MAX - 50] + "\n\n[Truncated]"
            await update.message.reply_text(output)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    except Exception as e:
        logger.exception("Document handler error")
        if update.message:
            await update.message.reply_text(f"Error: {str(e)[:500]}")


def get_handlers():
    return [
        CommandHandler("ingest", ingest_command),
        MessageHandler(filters.Document.ALL, document_handler),
    ]
