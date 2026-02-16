import asyncio
import json
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes
from src.database import SessionLocal
from src.models import UserSettings, IntegrationConfig

TELEGRAM_MAX = 4000

def is_authorized(user_id: int) -> bool:
    session = SessionLocal()
    try:
        settings = session.query(UserSettings).filter(UserSettings.user_id == user_id).first()
        return bool(settings and settings.authorized)
    finally:
        session.close()

def _get_user_settings(user_id: int):
    session = SessionLocal()
    try:
        return session.query(UserSettings).filter(UserSettings.user_id == user_id).first()
    finally:
        session.close()

def _save_integration_config(user_id: int, provider: str, config: dict):
    session = SessionLocal()
    try:
        existing = session.query(IntegrationConfig).filter(IntegrationConfig.user_id == user_id, IntegrationConfig.provider == provider).first()
        if existing:
            existing.config_json = json.dumps(config)
            existing.enabled = True
        else:
            entry = IntegrationConfig(user_id=user_id, provider=provider, config_json=json.dumps(config), enabled=True)
            session.add(entry)
        session.commit()
    finally:
        session.close()

def _update_user_setting(user_id: int, **kwargs):
    session = SessionLocal()
    try:
        settings = session.query(UserSettings).filter(UserSettings.user_id == user_id).first()
        if settings:
            for k, v in kwargs.items():
                setattr(settings, k, v)
            session.commit()
    finally:
        session.close()

async def connect_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /connect <service> <token/webhook>"""
    try:
        if not update.message or not update.effective_user:
            return
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("Authorization required.")
            return

        if not context.args or len(context.args) < 2:
            await update.message.reply_text(
                "EXTERNAL INTEGRATIONS\n" + "=" * 40 + "\n\n"
                "Usage: /connect <service> <token_or_webhook>\n\n"
                "Services:\n"
                "  slack <webhook_url> - Connect Slack incoming webhook\n"
                "  discord <webhook_url> - Connect Discord webhook\n"
                "  github <personal_access_token> - Connect GitHub\n"
                "  linear <api_key> - Connect Linear\n"
                "  calendar - Coming soon\n\n"
                "Other commands:\n"
                "  /slack <message> - Send to Slack\n"
                "  /discord <message> - Send to Discord\n"
                "  /ghissue <repo> <title> - Create GitHub issue\n"
                "  /ghissues <repo> - List GitHub issues\n"
                "  /linearissue <title> - Create Linear issue\n"
                "  /linearissues - List Linear issues\n"
                "  /integrations - Show connected services"
            )
            return

        service = context.args[0].lower()
        token = context.args[1]

        if service == "slack":
            await asyncio.to_thread(_update_user_setting, user_id, slack_webhook=token)
            await asyncio.to_thread(_save_integration_config, user_id, "slack", {"webhook": token})
            await update.message.reply_text("Slack webhook connected. Use /slack <message> to send.")
        elif service == "discord":
            await asyncio.to_thread(_update_user_setting, user_id, discord_webhook=token)
            await asyncio.to_thread(_save_integration_config, user_id, "discord", {"webhook": token})
            await update.message.reply_text("Discord webhook connected. Use /discord <message> to send.")
        elif service == "github":
            await asyncio.to_thread(_update_user_setting, user_id, github_token=token)
            await asyncio.to_thread(_save_integration_config, user_id, "github", {"token": token})
            await update.message.reply_text("GitHub connected. Use /ghissue or /ghissues to manage issues.")
        elif service == "linear":
            await asyncio.to_thread(_update_user_setting, user_id, linear_token=token)
            await asyncio.to_thread(_save_integration_config, user_id, "linear", {"api_key": token})
            await update.message.reply_text("Linear connected. Use /linearissue or /linearissues.")
        else:
            await update.message.reply_text(f"Unknown service: {service}. Supported: slack, discord, github, linear")
    except Exception as e:
        if update.message:
            await update.message.reply_text(f"Error: {str(e)[:500]}")

async def integrations_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show connected integrations."""
    try:
        if not update.message or not update.effective_user:
            return
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("Authorization required.")
            return

        settings = await asyncio.to_thread(_get_user_settings, user_id)
        lines = ["CONNECTED INTEGRATIONS", "=" * 40, ""]
        lines.append(f"Slack: {'Connected' if settings and settings.slack_webhook else 'Not connected'}")
        lines.append(f"Discord: {'Connected' if settings and settings.discord_webhook else 'Not connected'}")
        lines.append(f"GitHub: {'Connected' if settings and settings.github_token else 'Not connected'}")
        lines.append(f"Linear: {'Connected' if settings and settings.linear_token else 'Not connected'}")
        lines.append(f"Calendar: Coming soon")
        lines.extend(["", "Use /connect <service> <token> to connect a service."])
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        if update.message:
            await update.message.reply_text(f"Error: {str(e)[:500]}")

async def slack_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("Authorization required.")
            return
        if not context.args:
            await update.message.reply_text("Usage: /slack <message>")
            return
        settings = await asyncio.to_thread(_get_user_settings, user_id)
        if not settings or not settings.slack_webhook:
            await update.message.reply_text("Slack not connected. Use /connect slack <webhook_url>")
            return
        from src.services.integrations import IntegrationService
        message = " ".join(context.args)
        result = await IntegrationService.send_slack(settings.slack_webhook, message)
        if result["ok"]:
            await update.message.reply_text("Message sent to Slack.")
        else:
            await update.message.reply_text(f"Slack send failed: {result.get('error', 'unknown')}")
    except Exception as e:
        if update.message:
            await update.message.reply_text(f"Error: {str(e)[:500]}")

async def discord_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("Authorization required.")
            return
        if not context.args:
            await update.message.reply_text("Usage: /discord <message>")
            return
        settings = await asyncio.to_thread(_get_user_settings, user_id)
        if not settings or not settings.discord_webhook:
            await update.message.reply_text("Discord not connected. Use /connect discord <webhook_url>")
            return
        from src.services.integrations import IntegrationService
        message = " ".join(context.args)
        result = await IntegrationService.send_discord(settings.discord_webhook, message)
        if result["ok"]:
            await update.message.reply_text("Message sent to Discord.")
        else:
            await update.message.reply_text(f"Discord send failed: {result.get('error', 'unknown')}")
    except Exception as e:
        if update.message:
            await update.message.reply_text(f"Error: {str(e)[:500]}")

async def ghissue_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("Authorization required.")
            return
        if not context.args or len(context.args) < 2:
            await update.message.reply_text("Usage: /ghissue <owner/repo> <issue title>")
            return
        settings = await asyncio.to_thread(_get_user_settings, user_id)
        if not settings or not settings.github_token:
            await update.message.reply_text("GitHub not connected. Use /connect github <token>")
            return
        from src.services.integrations import IntegrationService
        repo = context.args[0]
        title = " ".join(context.args[1:])
        result = await IntegrationService.create_github_issue(settings.github_token, repo, title)
        if result["ok"]:
            await update.message.reply_text(f"GitHub issue created: {result.get('url', '')}")
        else:
            await update.message.reply_text(f"Failed: {result.get('error', 'unknown')}")
    except Exception as e:
        if update.message:
            await update.message.reply_text(f"Error: {str(e)[:500]}")

async def ghissues_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("Authorization required.")
            return
        if not context.args:
            await update.message.reply_text("Usage: /ghissues <owner/repo>")
            return
        settings = await asyncio.to_thread(_get_user_settings, user_id)
        if not settings or not settings.github_token:
            await update.message.reply_text("GitHub not connected. Use /connect github <token>")
            return
        from src.services.integrations import IntegrationService
        repo = context.args[0]
        result = await IntegrationService.list_github_issues(settings.github_token, repo)
        if not result["ok"]:
            await update.message.reply_text(f"Failed: {result.get('error', 'unknown')}")
            return
        issues = result.get("issues", [])
        if not issues:
            await update.message.reply_text(f"No open issues in {repo}.")
            return
        lines = [f"GITHUB ISSUES - {repo}", "=" * 40, ""]
        for i in issues:
            lines.append(f"#{i['number']} [{i['state']}] {i['title']}")
            lines.append(f"  {i['url']}")
        output = "\n".join(lines)
        if len(output) > TELEGRAM_MAX:
            output = output[:TELEGRAM_MAX - 50] + "\n\n[Truncated]"
        await update.message.reply_text(output)
    except Exception as e:
        if update.message:
            await update.message.reply_text(f"Error: {str(e)[:500]}")

async def linearissue_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("Authorization required.")
            return
        if not context.args:
            await update.message.reply_text("Usage: /linearissue <title>")
            return
        settings = await asyncio.to_thread(_get_user_settings, user_id)
        if not settings or not settings.linear_token:
            await update.message.reply_text("Linear not connected. Use /connect linear <api_key>")
            return
        from src.services.integrations import IntegrationService
        title = " ".join(context.args)
        result = await IntegrationService.create_linear_issue(settings.linear_token, title)
        if result["ok"]:
            await update.message.reply_text(f"Linear issue created: {result.get('id', '')} {result.get('url', '')}")
        else:
            await update.message.reply_text(f"Failed: {result.get('error', 'unknown')}")
    except Exception as e:
        if update.message:
            await update.message.reply_text(f"Error: {str(e)[:500]}")

async def linearissues_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("Authorization required.")
            return
        settings = await asyncio.to_thread(_get_user_settings, user_id)
        if not settings or not settings.linear_token:
            await update.message.reply_text("Linear not connected. Use /connect linear <api_key>")
            return
        from src.services.integrations import IntegrationService
        result = await IntegrationService.list_linear_issues(settings.linear_token)
        if not result["ok"]:
            await update.message.reply_text(f"Failed: {result.get('error', 'unknown')}")
            return
        issues = result.get("issues", [])
        if not issues:
            await update.message.reply_text("No Linear issues found.")
            return
        lines = ["LINEAR ISSUES", "=" * 40, ""]
        for i in issues:
            lines.append(f"{i['id']} [{i['state']}] {i['title']}")
            lines.append(f"  {i['url']}")
        output = "\n".join(lines)
        if len(output) > TELEGRAM_MAX:
            output = output[:TELEGRAM_MAX - 50] + "\n\n[Truncated]"
        await update.message.reply_text(output)
    except Exception as e:
        if update.message:
            await update.message.reply_text(f"Error: {str(e)[:500]}")

async def pipeline_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /pipeline commands."""
    try:
        if not update.message or not update.effective_user:
            return
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("Authorization required.")
            return

        from src.services.pipelines import create_pipeline, get_user_pipelines, delete_pipeline, toggle_pipeline

        if not context.args:
            pipelines = await asyncio.to_thread(get_user_pipelines, user_id)
            lines = ["PIPELINE TRIGGERS", "=" * 40, ""]
            if pipelines:
                for p in pipelines:
                    status = "ON" if p["enabled"] else "OFF"
                    lines.append(f"#{p['id']} [{status}] Trigger: /{p['trigger_command']}")
                    actions = p.get("actions", [])
                    for a in actions:
                        lines.append(f"  -> {a}")
                    lines.append("")
            else:
                lines.append("No pipelines configured.")
            lines.extend(["", "Usage:", "/pipeline add <trigger_cmd> <action1> | <action2> | ...", "/pipeline toggle <id>", "/pipeline delete <id>", "", "Example:", "/pipeline add extract slack {result} | ghissue owner/repo {summary}"])
            output = "\n".join(lines)
            if len(output) > TELEGRAM_MAX:
                output = output[:TELEGRAM_MAX - 50] + "\n\n[Truncated]"
            await update.message.reply_text(output)
            return

        action = context.args[0].lower()

        if action == "add" and len(context.args) >= 3:
            trigger = context.args[1]
            actions_str = " ".join(context.args[2:])
            actions = [a.strip() for a in actions_str.split("|") if a.strip()]
            if not actions:
                await update.message.reply_text("No actions specified. Separate with |")
                return
            pipe_id = await asyncio.to_thread(create_pipeline, user_id, trigger, actions)
            await update.message.reply_text(f"Pipeline #{pipe_id} created.\nTrigger: /{trigger}\nActions: {' -> '.join(actions)}")

        elif action == "toggle" and len(context.args) >= 2:
            try:
                pipe_id = int(context.args[1])
            except ValueError:
                await update.message.reply_text("Pipeline ID must be a number.")
                return
            result = await asyncio.to_thread(toggle_pipeline, pipe_id, user_id)
            await update.message.reply_text(result)

        elif action == "delete" and len(context.args) >= 2:
            try:
                pipe_id = int(context.args[1])
            except ValueError:
                await update.message.reply_text("Pipeline ID must be a number.")
                return
            result = await asyncio.to_thread(delete_pipeline, pipe_id, user_id)
            if result:
                await update.message.reply_text(f"Pipeline #{pipe_id} deleted.")
            else:
                await update.message.reply_text(f"Pipeline #{pipe_id} not found.")
        else:
            await update.message.reply_text("Usage: /pipeline add|toggle|delete <args>")
    except Exception as e:
        if update.message:
            await update.message.reply_text(f"Error: {str(e)[:500]}")

def get_handlers():
    return [
        CommandHandler("connect", connect_command),
        CommandHandler("integrations", integrations_command),
        CommandHandler("slack", slack_command),
        CommandHandler("discord", discord_command),
        CommandHandler("ghissue", ghissue_command),
        CommandHandler("ghissues", ghissues_command),
        CommandHandler("linearissue", linearissue_command),
        CommandHandler("linearissues", linearissues_command),
        CommandHandler("pipeline", pipeline_command),
    ]
