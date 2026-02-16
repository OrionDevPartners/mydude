import time
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from src.database import SessionLocal
from src.models import UserSettings


def is_authorized(user_id: int) -> bool:
    session = SessionLocal()
    try:
        settings = session.query(UserSettings).filter(UserSettings.user_id == user_id).first()
        if settings and settings.authorized:
            return True
        return False
    finally:
        session.close()


def _format_timestamp(ts):
    if not ts:
        return "never"
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S UTC")


async def selfheal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return

        if not is_authorized(update.effective_user.id):
            await update.message.reply_text("Authorization required. Use /authorize <password> first.")
            return

        lines = ["=== SELF-HEALING PROTOCOL STATUS ===", ""]

        cb = context.bot_data.get("circuit_breaker")
        if cb:
            cb_status = await cb.get_status()
            lines.append("-- Circuit Breaker --")
            if cb_status:
                for provider, info in cb_status.items():
                    lines.append(
                        f"  {provider}: state={info['state']} "
                        f"failures={info['failure_count']} "
                        f"successes={info['success_count']} "
                        f"avg_latency={info['avg_latency']}s"
                    )
                    if info.get("last_error"):
                        lines.append(f"    last_error: {str(info['last_error'])[:100]}")
            else:
                lines.append("  No providers tracked yet")
            lines.append("")

        hm = context.bot_data.get("health_monitor")
        if hm:
            hm_status = hm.get_status()
            lines.append("-- Health Monitor --")
            if hm_status:
                for component, info in hm_status.items():
                    if component == "llm_providers" and "providers" in info:
                        lines.append(f"  {component}: {info.get('status', 'unknown')} - {info.get('details', '')}")
                    else:
                        lines.append(
                            f"  {component}: {info.get('status', 'unknown')} "
                            f"(checked: {_format_timestamp(info.get('last_check'))}) "
                            f"- {info.get('details', '')}"
                        )
            else:
                lines.append("  No checks run yet")
        else:
            lines.append("-- Health Monitor: not running --")

        await update.message.reply_text("\n".join(lines))

    except Exception as e:
        if update.message:
            await update.message.reply_text(f"Error getting self-heal status: {str(e)[:500]}")


async def healcheck_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return

        if not is_authorized(update.effective_user.id):
            await update.message.reply_text("Authorization required. Use /authorize <password> first.")
            return

        hm = context.bot_data.get("health_monitor")
        if not hm:
            await update.message.reply_text("Health monitor is not running.")
            return

        await update.message.reply_text("Running health checks...")

        results = await hm.run_checks()

        lines = ["=== HEALTH CHECK RESULTS ===", ""]
        for component, info in results.items():
            status = info.get("status", "unknown")
            details = info.get("details", "")
            checked = _format_timestamp(info.get("last_check"))
            icon = "OK" if status == "healthy" else ("WARN" if status == "degraded" else "FAIL")
            lines.append(f"[{icon}] {component}: {status}")
            lines.append(f"     {details}")
            lines.append(f"     checked: {checked}")
            lines.append("")

        await update.message.reply_text("\n".join(lines))

    except Exception as e:
        if update.message:
            await update.message.reply_text(f"Error running health check: {str(e)[:500]}")


def get_handlers():
    return [
        CommandHandler("selfheal", selfheal_command),
        CommandHandler("healcheck", healcheck_command),
    ]
