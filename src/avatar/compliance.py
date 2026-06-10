"""AI-use disclosure + recording-consent enforcement for avatar call flows.

Disclosure and consent are MANDATORY in any AI sales/coaching call (task #72
architectural constraint). This module owns the canonical disclosure/consent text
and the gate that refuses to let a session go active without them — fail loud
(governance pillar #1), never silently proceed.
"""

# Canonical disclosure text. Kept as a constant (single source of truth) rather
# than a managed setting; ``{product}`` is filled with the product name.
AI_DISCLOSURE_TEMPLATE = (
    "Heads up: you're speaking with an AI assistant from {product}, not a human. "
    "It can make mistakes, and anything it commits to will be confirmed by a person "
    "before it's binding."
)

RECORDING_CONSENT_PROMPT = (
    "This call may be recorded and processed by AI for quality, training, and "
    "compliance. Do you consent to continue?"
)


class DisclosureRequired(RuntimeError):
    """A session tried to go active without the AI-use disclosure being shown."""


class ConsentRequired(RuntimeError):
    """A session tried to go active without recording consent being granted."""


def _product_name():
    try:
        from src.web.branding import PRODUCT_NAME  # single source of truth
        if PRODUCT_NAME:
            return PRODUCT_NAME
    except Exception:  # noqa: BLE001 — branding is best-effort
        pass
    return "MyDude.io"


def disclosure_text(product_name=None):
    return AI_DISCLOSURE_TEMPLATE.format(product=product_name or _product_name())


def consent_prompt():
    return RECORDING_CONSENT_PROMPT


def ensure_call_compliance(profile, session):
    """Refuse (fail loud) if a session is about to go active without the required
    disclosure shown and recording consent granted. Defense-in-depth: callers also
    gate at the state-machine level, but this re-checks the STORED session."""
    if getattr(profile, "disclosure_required", True) and not getattr(
            session, "disclosure_shown", False):
        raise DisclosureRequired(
            "AI-use disclosure must be shown before this session can go active.")
    if getattr(profile, "consent_required", True) and getattr(
            session, "consent_status", "pending") != "granted":
        raise ConsentRequired(
            "Recording consent must be granted before this session can go active.")
