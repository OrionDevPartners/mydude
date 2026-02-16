import os
import asyncio
import logging
import tempfile
from telegram import Update
from telegram.ext import MessageHandler, ContextTypes, filters
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


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("Authorization required to use voice notes. Use /authorize <password>.")
            return

        voice = update.message.voice or update.message.audio
        if not voice:
            return

        await update.message.reply_text("Downloading and transcribing voice note...")

        file = await context.bot.get_file(voice.file_id)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            await file.download_to_drive(tmp_path)

            from src.services.voice import transcribe_voice
            transcript = await transcribe_voice(tmp_path)

            from src.services.memory import store_memory
            from src.services.audit import log_command
            await asyncio.to_thread(store_memory, user_id, "voice", transcript, transcript[:200], "")
            await asyncio.to_thread(log_command, user_id, "voice", f"duration={voice.duration}s", "ok", transcript[:200])

            output = f"VOICE TRANSCRIPTION\n{'=' * 40}\n\n{transcript}"

            caption = (update.message.caption or "").lower()
            if "analyze" in caption or "extract" in caption:
                llm = context.bot_data.get("llm_instance")
                if llm:
                    await update.message.reply_text("Running swarm analysis on transcript...")
                    try:
                        result = await llm.call_team(
                            "You are an expert analyst. Extract key points, action items, and insights from this voice memo transcript.",
                            f"TRANSCRIPT:\n{transcript[:6000]}",
                            roles_hint={"openai": "action items", "anthropic": "key decisions", "gemini": "insights", "grok": "creative ideas"}
                        )
                        merged = result.get("merged", "")
                        await asyncio.to_thread(store_memory, user_id, "voice_analysis", merged, merged[:200], "")
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
        logger.exception("Voice handler error")
        if update.message:
            await update.message.reply_text(f"Voice note error: {str(e)[:500]}")


def get_handlers():
    return [
        MessageHandler(filters.VOICE | filters.AUDIO, voice_handler),
    ]
