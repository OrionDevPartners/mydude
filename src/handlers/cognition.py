from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from src.database import SessionLocal
from src.models import UserSettings
from src.swarm.constitution import (
    CONSTITUTION_RULES,
    EpistemicCategory,
    ReasoningMode,
    BANNED_PHRASES,
)
from src.swarm.compliance import compute_compliance_score, ComplianceMetrics
from src.swarm.hallucination import HallucinationFeatures, compute_hallucination_risk
from src.swarm.contract import CognitiveRole, DebateRound

TELEGRAM_MAX = 4096


def is_authorized(user_id: int) -> bool:
    session = SessionLocal()
    try:
        settings = session.query(UserSettings).filter(UserSettings.user_id == user_id).first()
        if settings and settings.authorized:
            return True
        return False
    finally:
        session.close()


async def constitution_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return

        user_id = update.effective_user.id

        if not is_authorized(user_id):
            await update.message.reply_text(
                "You must be authorized to use this command. Use /authorize <password> first."
            )
            return

        # Gather epistemic categories
        epistemic_cats = ", ".join([cat.value for cat in EpistemicCategory])

        # Gather reasoning modes
        reasoning_modes = ", ".join([mode.value for mode in ReasoningMode])

        # Gather banned phrases
        banned = "\n  ".join(BANNED_PHRASES)

        text = (
            "AGENT CONSTITUTION\n\n"
            f"{CONSTITUTION_RULES}\n\n"
            "EPISTEMIC CATEGORIES:\n"
            f"  {epistemic_cats}\n\n"
            "REASONING MODES:\n"
            f"  {reasoning_modes}\n\n"
            "BANNED PHRASES:\n"
            f"  {banned}"
        )

        await update.message.reply_text(text[:TELEGRAM_MAX])

    except Exception as e:
        if update.message:
            await update.message.reply_text(f"Error: {e}")


async def cognition_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return

        user_id = update.effective_user.id

        if not is_authorized(user_id):
            await update.message.reply_text(
                "You must be authorized to use this command. Use /authorize <password> first."
            )
            return

        # Build cognitive roles list
        roles = "\n  ".join([f"{role.name}: {role.value}" for role in CognitiveRole])

        # Build debate rounds summary
        rounds = "\n  ".join(
            [f"Round {round.value}: {round.name}" for round in DebateRound]
        )

        # Build compliance scoring formula summary
        compliance_formula = (
            "cs = 100 - 8*u - 6*(l-e) - 12*c - 5*d - 7*m - 4*r - 6*x\n"
            "  u=unlabeled_claims, l=load_bearing_claims, e=evidenced_claims,\n"
            "  c=constraint_violations, d=drift_events, m=mode_mixing_events,\n"
            "  r=missing_required_fields, x=uncited_external_claims"
        )

        # Build hallucination risk model features
        hallucination_features = (
            "Weights (sum=1.0):\n"
            "  0.22 * unlabeled_ratio (claims lacking epistemic labels)\n"
            "  0.22 * unevidenced_ratio (load-bearing claims without evidence)\n"
            "  0.12 * mode_mixing_rate (analytic/exploratory mixing)\n"
            "  0.10 * constraint_pressure (budget/policy constraint load)\n"
            "  0.10 * novelty_pressure (pressure to generate new content)\n"
            "  0.10 * external_dependency (reliance on external sources)\n"
            "  0.08 * disagreement_index (agent consensus failure rate)\n"
            "  0.06 * overconfidence_delta (confidence exceeds evidence strength)"
        )

        text = (
            "COGNITIVE ARCHITECTURE STATUS\n\n"
            "Constitution version: v1.0\n\n"
            "COMPLIANCE SCORING:\n"
            f"{compliance_formula}\n\n"
            "HALLUCINATION RISK MODEL:\n"
            f"{hallucination_features}\n\n"
            "SWARM CONTRACT - COGNITIVE ROLES:\n"
            f"  {roles}\n\n"
            "DEBATE CYCLE - 7 ROUNDS:\n"
            f"  {rounds}"
        )

        await update.message.reply_text(text[:TELEGRAM_MAX])

    except Exception as e:
        if update.message:
            await update.message.reply_text(f"Error: {e}")


def get_handlers():
    return [
        CommandHandler("constitution", constitution_command),
        CommandHandler("cognition", cognition_command),
    ]
