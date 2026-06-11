"""Production gate for the experimental embedded memory stack.

The memory stack is **referenced but not deployed for production**: this module
is the single chokepoint that decides whether the stack is allowed to initialize
in the current environment.

Rules
-----
* In a Replit *deployment* (``REPLIT_DEPLOYMENT == "1"``) the stack is disabled
  by default. Importing the package is always fine; *initializing* it is not.
* In development the stack is enabled by default.
* ``AGENT_MEMORY_STACK`` is an explicit override in either direction:
      AGENT_MEMORY_STACK=1  -> force-enable  (even inside a deployment)
      AGENT_MEMORY_STACK=0  -> force-disable (even in development)
* Callers may pass ``force=True`` to :func:`require_enabled` to bypass the gate
  deliberately (e.g. an opt-in production experiment). This is explicit and
  auditable rather than implicit.

This keeps the experimental code honest with MyDude's governance pillars: the
implementation is real and fully functional, but it cannot silently ride along
into a production deployment.
"""

from __future__ import annotations

import os

_TRUE = {"1", "true", "on", "yes"}
_FALSE = {"0", "false", "off", "no"}


class ProductionGuardError(RuntimeError):
    """Raised when the experimental memory stack is initialized while disabled."""


def is_production() -> bool:
    """True when running inside a Replit deployment."""
    return os.environ.get("REPLIT_DEPLOYMENT") == "1"


def _override() -> bool | None:
    """Return the explicit AGENT_MEMORY_STACK override, or None if unset."""
    raw = os.environ.get("AGENT_MEMORY_STACK")
    if raw is None:
        return None
    val = raw.strip().lower()
    if val in _TRUE:
        return True
    if val in _FALSE:
        return False
    raise ValueError(
        f"AGENT_MEMORY_STACK must be one of {_TRUE | _FALSE}, got {raw!r}"
    )


def is_enabled() -> bool:
    """Whether the stack is allowed to initialize in the current environment."""
    override = _override()
    if override is not None:
        return override
    return not is_production()


def require_enabled(force: bool = False) -> None:
    """Fail loud if the stack is not allowed to initialize.

    Parameters
    ----------
    force:
        Bypass the gate deliberately. Use only for explicit, auditable
        opt-in experiments.
    """
    if force or is_enabled():
        return
    reason = (
        "production deployment" if is_production() else "AGENT_MEMORY_STACK override"
    )
    raise ProductionGuardError(
        "The experimental embedded memory stack is disabled "
        f"({reason}). It is dev-only by design: set AGENT_MEMORY_STACK=1 or pass "
        "force=True to initialize it deliberately."
    )
