import os
import asyncio
import logging
import tempfile
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


async def transcribe_voice(file_path: str) -> str:
    """Transcribe an audio file using OpenAI Whisper."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return "OpenAI API key not configured. Cannot transcribe."

    client = AsyncOpenAI(api_key=api_key)
    try:
        with open(file_path, "rb") as audio_file:
            transcript = await client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
            )
        return transcript.text
    except Exception as e:
        logger.warning(f"Transcription failed: {e}")
        return f"Transcription failed: {str(e)[:200]}"
