import os
import asyncio
import logging
import subprocess
import re

logger = logging.getLogger(__name__)

WORKSPACE = "/home/runner/workspace"
IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".pythonlibs", ".local", ".cache", ".upm", ".config"}
CODE_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".json", ".yaml", ".yml", ".md", ".txt", ".sql", ".sh", ".toml", ".cfg", ".ini", ".env.example"}

async def search_codebase(query: str, max_results: int = 10) -> list:
    """Search codebase using grep for relevant files."""
    try:
        def _search():
            results = []
            try:
                cmd = ["grep", "-rl", "--include=*.py", "--include=*.js", "--include=*.ts",
                       "--include=*.html", "--include=*.css", "--include=*.json",
                       "--include=*.md", "--include=*.yaml", "--include=*.yml",
                       "-i", "--", query, WORKSPACE]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                files = result.stdout.strip().split("\n")[:max_results]
                for f in files:
                    if f and not any(d in f for d in IGNORE_DIRS):
                        results.append(f)
            except Exception:
                pass
            return results
        return await asyncio.to_thread(_search)
    except Exception as e:
        logger.warning(f"Codebase search failed: {e}")
        return []

async def get_file_content(file_path: str, max_lines: int = 100) -> str:
    """Read file content with line limit."""
    try:
        def _read():
            with open(file_path, 'r', errors='replace') as f:
                lines = f.readlines()[:max_lines]
                return "".join(lines)
        return await asyncio.to_thread(_read)
    except Exception as e:
        return f"Error reading {file_path}: {e}"

async def get_project_structure() -> str:
    """Get a tree-like project structure."""
    try:
        def _tree():
            lines = []
            for root, dirs, files in os.walk(WORKSPACE):
                dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
                level = root.replace(WORKSPACE, "").count(os.sep)
                indent = "  " * level
                basename = os.path.basename(root) or "."
                lines.append(f"{indent}{basename}/")
                if level < 3:
                    sub_indent = "  " * (level + 1)
                    for f in sorted(files)[:20]:
                        if any(f.endswith(ext) for ext in CODE_EXTENSIONS):
                            lines.append(f"{sub_indent}{f}")
            return "\n".join(lines[:100])
        return await asyncio.to_thread(_tree)
    except Exception as e:
        return f"Error: {e}"

async def build_rag_context(query: str) -> str:
    """Build context from codebase for RAG query."""
    matching_files = await search_codebase(query)

    context_parts = []
    total_chars = 0
    max_chars = 8000

    for fpath in matching_files[:5]:
        if total_chars >= max_chars:
            break
        content = await get_file_content(fpath, max_lines=50)
        relative = fpath.replace(WORKSPACE + "/", "")
        chunk = f"--- {relative} ---\n{content}\n"
        context_parts.append(chunk)
        total_chars += len(chunk)

    if not context_parts:
        structure = await get_project_structure()
        return f"No direct matches found. Project structure:\n{structure}"

    return "\n".join(context_parts)
