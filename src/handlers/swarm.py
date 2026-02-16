from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from src.swarm.policy import PolicyEngine
from src.swarm.integrations import Integrations
from src.swarm.broker import CapabilityBroker
from src.swarm.orchestrator import WaveOrchestrator
from src.swarm.utils import safe_json_dumps
from src.database import SessionLocal
from src.models import UserSettings

TELEGRAM_MAX = 3500

policy = PolicyEngine()
integrations = Integrations()
broker = CapabilityBroker(policy, integrations)
orchestrator = WaveOrchestrator(broker)


def is_authorized(user_id: int) -> bool:
    session = SessionLocal()
    try:
        settings = session.query(UserSettings).filter(UserSettings.user_id == user_id).first()
        if settings and settings.authorized:
            return True
        return False
    finally:
        session.close()


async def goal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return

        user_id = update.effective_user.id

        if not is_authorized(user_id):
            await update.message.reply_text(
                "You must be authorized to use the swarm. Use /authorize <password> first."
            )
            return

        if not context.args:
            await update.message.reply_text(
                "Usage: /goal <your objective>\n\n"
                "Example: /goal Build a REST API for user management with auth and deploy to staging"
            )
            return

        goal_text = " ".join(context.args)
        await update.message.reply_text(
            f"PORTER SWARM ACTIVATED\n\n"
            f"Goal: {goal_text}\n\n"
            f"Running cascading waves with bounded concurrency...\n"
            f"This may take a moment."
        )

        result = await orchestrator.run(goal_text)
        output = safe_json_dumps(result, limit=TELEGRAM_MAX)
        await update.message.reply_text(f"SWARM RESULT:\n\n{output}")

    except Exception as e:
        if update.message:
            await update.message.reply_text(f"Swarm error: {type(e).__name__}: {str(e)[:500]}")


async def waves_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message:
            return

        import os
        providers = {
            "OpenAI": bool(os.getenv("OPENAI_API_KEY")),
            "Anthropic": bool(os.getenv("ANTHROPIC_API_KEY")),
            "Gemini": bool(os.getenv("GEMINI_API_KEY")),
            "Grok": bool(os.getenv("GROK_API_KEY")),
        }
        provider_lines = "\n".join(
            f"  {name}: {'ACTIVE' if active else 'not configured'}"
            for name, active in providers.items()
        )
        llm_mode = os.getenv("LLM_PROVIDER", "stub")
        info = (
            "PORTER SWARM STATUS\n\n"
            f"LLM Mode: {llm_mode}\n"
            f"Wave Concurrency: {os.getenv('WAVE_CONCURRENCY', '12')}\n"
            f"Agents Per Wave: {os.getenv('AGENTS_PER_WAVE', '60')}\n"
            f"Max Waves: {os.getenv('MAX_WAVES', '4')}\n"
            f"Production Policy: {'ENABLED' if policy.allow_prod else 'BLOCKED'}\n\n"
            f"LLM Providers:\n{provider_lines}\n\n"
            "Capabilities available:\n"
            "- git_status\n"
            "- terraform_plan\n"
            "- terraform_apply (requires plan first)\n"
            "- asana_query\n"
            "- op_read_scoped\n\n"
            "Use /goal <objective> to launch a swarm run.\n"
            "Set LLM_PROVIDER=multi to use multi-provider LLM team."
        )
        await update.message.reply_text(info)
    except Exception as e:
        if update.message:
            await update.message.reply_text(f"Error: {e}")


async def policy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return

        if not is_authorized(update.effective_user.id):
            await update.message.reply_text("Authorization required.")
            return

        if not context.args:
            status = "ENABLED" if policy.allow_prod else "BLOCKED"
            await update.message.reply_text(
                f"Current production policy: {status}\n\n"
                "Usage:\n"
                "/policy enable_prod - Allow production actions\n"
                "/policy disable_prod - Block production actions"
            )
            return

        action = context.args[0].lower()
        if action == "enable_prod":
            policy.allow_prod = True
            await update.message.reply_text("Production actions are now ENABLED. Be careful.")
        elif action == "disable_prod":
            policy.allow_prod = False
            await update.message.reply_text("Production actions are now BLOCKED (default safe state).")
        else:
            await update.message.reply_text("Unknown action. Use: enable_prod or disable_prod")
    except Exception as e:
        if update.message:
            await update.message.reply_text(f"Error: {e}")


def get_handlers():
    return [
        CommandHandler("goal", goal_command),
        CommandHandler("waves", waves_command),
        CommandHandler("policy", policy_command),
    ]
