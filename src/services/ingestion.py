import os
import asyncio
import logging
import requests
import re

logger = logging.getLogger(__name__)


async def ingest_url(url: str) -> str:
    """Fetch and extract text content from a URL."""
    try:
        def _fetch():
            headers = {"User-Agent": "Mozilla/5.0 (Bot)"}
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "text/html" in content_type:
                text = _strip_html(resp.text)
            else:
                text = resp.text
            return text[:15000]
        return await asyncio.to_thread(_fetch)
    except Exception as e:
        return f"Failed to fetch URL: {str(e)[:200]}"


def _strip_html(html: str) -> str:
    """Basic HTML tag stripping."""
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


async def ingest_file(file_path: str) -> str:
    """Extract text from a downloaded file."""
    try:
        def _read():
            with open(file_path, 'r', errors='replace') as f:
                return f.read()[:15000]
        return await asyncio.to_thread(_read)
    except Exception as e:
        return f"Failed to read file: {str(e)[:200]}"
