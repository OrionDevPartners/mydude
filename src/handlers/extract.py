import os
import asyncio
import logging
import re
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes
from src.database import SessionLocal
from src.models import UserSettings

logger = logging.getLogger(__name__)

TELEGRAM_MAX = 4000


def is_authorized(user_id: int) -> bool:
    session = SessionLocal()
    try:
        settings = session.query(UserSettings).filter(UserSettings.user_id == user_id).first()
        if settings and settings.authorized:
            return True
        return False
    finally:
        session.close()


def _extract_action_items(text: str) -> list:
    items = []
    lines = text.split("\n")
    capture = False
    for line in lines:
        stripped = line.strip()
        upper = stripped.upper()
        if "ACTION ITEM" in upper or "ACTION_ITEM" in upper or "TASKS" in upper:
            capture = True
            continue
        if capture:
            if stripped.startswith(("-", "*", "•")) or re.match(r"^\d+[\.\)]\s", stripped):
                clean = re.sub(r"^[-*•]\s*", "", stripped)
                clean = re.sub(r"^\d+[\.\)]\s*", "", clean)
                if clean and len(clean) > 3:
                    items.append(clean[:200])
            elif stripped == "":
                continue
            elif any(kw in upper for kw in ["DECISION", "INSIGHT", "RISK", "BLOCKER", "FOLLOW", "RESULT", "ARTIFACT", "CHECK"]):
                capture = False
    return items


async def extract_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return

        user_id = update.effective_user.id

        if not is_authorized(user_id):
            await update.message.reply_text(
                "You must be authorized to use /extract. Use /authorize <password> first."
            )
            return

        if not context.args:
            await update.message.reply_text(
                "EXTRACT - Actionable Intelligence Extractor\n"
                "=" * 40 + "\n\n"
                "Usage: /extract <paste your text content here>\n\n"
                "Paste a conversation transcript, meeting notes, or any text.\n"
                "The 4-provider AI swarm will extract:\n"
                "- Key decisions\n"
                "- Action items (with owners)\n"
                "- Insights and learnings\n"
                "- Risks and blockers\n"
                "- Follow-ups needed\n\n"
                "Optionally creates Asana tasks from action items.\n"
                "Use /setproject <gid> to configure your Asana project."
            )
            return

        content = " ".join(context.args)

        if len(content) < 20:
            await update.message.reply_text(
                "Content too short. Please paste at least a paragraph of text to analyze."
            )
            return

        await update.message.reply_text(
            "Analyzing content with 4-provider swarm...\n"
            "This may take 15-30 seconds."
        )

        llm = context.bot_data.get("llm_instance")
        if not llm:
            try:
                from src.swarm.llm_multi import MultiProviderLLM
                llm = MultiProviderLLM()
                context.bot_data["llm_instance"] = llm
            except Exception as e:
                await update.message.reply_text(
                    f"Could not initialize LLM providers: {str(e)[:200]}"
                )
                return

        system_prompt = (
            "You are an expert analyst specializing in extracting actionable intelligence "
            "from conversation transcripts, meeting notes, and text content. "
            "Be precise, concise, and focus on extractable value. "
            "Format your output with clear sections using these exact headers:\n"
            "KEY DECISIONS:\n"
            "ACTION ITEMS:\n"
            "INSIGHTS AND LEARNINGS:\n"
            "RISKS AND BLOCKERS:\n"
            "FOLLOW-UPS:\n"
        )

        user_prompt = (
            "Analyze the following content and extract actionable intelligence.\n\n"
            "For each section, use bullet points. For action items, include the owner "
            "in parentheses if mentioned.\n\n"
            "CONTENT TO ANALYZE:\n"
            "---\n"
            f"{content[:8000]}\n"
            "---\n\n"
            "Extract: key decisions, action items (with owners if mentioned), "
            "insights/learnings, risks/blockers, and follow-ups."
        )

        roles = {
            "openai": "action items expert",
            "anthropic": "risk/decision analyst",
            "gemini": "insight extractor",
            "grok": "creative opportunities finder",
        }

        try:
            result = await llm.call_team(system_prompt, user_prompt, roles_hint=roles)
        except Exception as e:
            await update.message.reply_text(
                f"Swarm analysis failed: {type(e).__name__}: {str(e)[:300]}"
            )
            return

        merged = result.get("merged", "")
        replies = result.get("replies", [])

        providers_used = []
        for r in replies:
            status = "OK" if r.ok else "FAIL"
            providers_used.append(f"{r.provider} ({r.model}): {status}")

        asana_status = ""
        asana_pat = os.getenv("ASANA_PAT")
        if asana_pat:
            try:
                action_items = _extract_action_items(merged)
                if action_items:
                    session = SessionLocal()
                    try:
                        settings = session.query(UserSettings).filter(
                            UserSettings.user_id == user_id
                        ).first()
                        project_gid = settings.asana_project_gid if settings else None
                    finally:
                        session.close()

                    from src.asana_client import AsanaClient
                    client = AsanaClient(asana_pat)

                    if not project_gid:
                        ws = await asyncio.to_thread(client.get_default_workspace)
                        if ws:
                            proj = await asyncio.to_thread(client.get_default_project, ws.get("gid", ""))
                            if proj:
                                project_gid = proj.get("gid")

                    if project_gid:
                        tasks_to_create = [{"name": item} for item in action_items[:20]]
                        created = await asyncio.to_thread(
                            client.create_tasks_batch, project_gid, tasks_to_create
                        )
                        success_count = sum(1 for t in created if "error" not in t)
                        asana_status = (
                            f"\n\nASANA INTEGRATION\n"
                            f"{'-' * 30}\n"
                            f"Created {success_count}/{len(action_items)} tasks in Asana"
                        )
                    else:
                        asana_status = (
                            "\n\nASANA INTEGRATION\n"
                            f"{'-' * 30}\n"
                            "No Asana project configured. Use /setproject <gid> to set one."
                        )
                else:
                    asana_status = (
                        "\n\nASANA INTEGRATION\n"
                        f"{'-' * 30}\n"
                        "No action items detected for Asana task creation."
                    )
            except Exception as e:
                asana_status = (
                    "\n\nASANA INTEGRATION\n"
                    f"{'-' * 30}\n"
                    f"Asana error: {str(e)[:200]}"
                )

        provider_info = "\n".join(providers_used)
        output = (
            f"EXTRACTION RESULTS\n"
            f"{'=' * 40}\n\n"
            f"PROVIDERS CONSULTED\n"
            f"{'-' * 30}\n"
            f"{provider_info}\n\n"
            f"MERGED ANALYSIS\n"
            f"{'-' * 30}\n"
            f"{merged}"
            f"{asana_status}"
        )

        if len(output) > TELEGRAM_MAX:
            output = output[:TELEGRAM_MAX - 50] + "\n\n[Truncated - output too long]"

        await update.message.reply_text(output)

    except Exception as e:
        logger.exception("Error in /extract command")
        if update.message:
            await update.message.reply_text(
                f"Extract error: {type(e).__name__}: {str(e)[:500]}"
            )


async def setproject_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return

        user_id = update.effective_user.id

        if not is_authorized(user_id):
            await update.message.reply_text(
                "You must be authorized to use /setproject. Use /authorize <password> first."
            )
            return

        if not context.args:
            asana_pat = os.getenv("ASANA_PAT")
            if not asana_pat:
                await update.message.reply_text(
                    "ASANA PROJECT SETUP\n"
                    f"{'=' * 40}\n\n"
                    "ASANA_PAT is not configured.\n"
                    "Add your Asana Personal Access Token to 1Password vault first.\n\n"
                    "Usage: /setproject <project_gid>"
                )
                return

            from src.asana_client import AsanaClient
            client = AsanaClient(asana_pat)

            try:
                workspaces = await asyncio.to_thread(client.get_workspaces)
            except Exception as e:
                await update.message.reply_text(f"Error connecting to Asana: {str(e)[:300]}")
                return

            if not workspaces:
                await update.message.reply_text(
                    "No Asana workspaces found. Check your ASANA_PAT token."
                )
                return

            lines = ["ASANA WORKSPACES AND PROJECTS", "=" * 40, ""]
            for ws in workspaces:
                lines.append(f"Workspace: {ws.get('name', 'Unknown')} (gid: {ws.get('gid', '')})")
                try:
                    projects = await asyncio.to_thread(client.get_projects, ws.get("gid", ""))
                    if projects:
                        for p in projects[:20]:
                            lines.append(f"  - {p.get('name', 'Unknown')} (gid: {p.get('gid', '')})")
                    else:
                        lines.append("  (no projects)")
                except Exception:
                    lines.append("  (error loading projects)")
                lines.append("")

            lines.append("Usage: /setproject <project_gid>")
            output = "\n".join(lines)

            if len(output) > TELEGRAM_MAX:
                output = output[:TELEGRAM_MAX - 50] + "\n\n[Truncated]"

            await update.message.reply_text(output)
            return

        project_gid = context.args[0].strip()

        session = SessionLocal()
        try:
            settings = session.query(UserSettings).filter(
                UserSettings.user_id == user_id
            ).first()
            if not settings:
                settings = UserSettings(user_id=user_id, asana_project_gid=project_gid)
                session.add(settings)
            else:
                settings.asana_project_gid = project_gid
            session.commit()
        finally:
            session.close()

        await update.message.reply_text(
            f"Asana project GID set to: {project_gid}\n\n"
            "Future /extract commands will create tasks in this project."
        )

    except Exception as e:
        logger.exception("Error in /setproject command")
        if update.message:
            await update.message.reply_text(
                f"Error: {type(e).__name__}: {str(e)[:500]}"
            )


def get_handlers():
    return [
        CommandHandler("extract", extract_command),
        CommandHandler("setproject", setproject_command),
    ]
