"""Realtime / telephony capability adapter.

Wraps the existing ``src.telephony.facade`` module behind the unified
CapabilityAdapter interface. The facade is already vendor-agnostic (Twilio is
the current backend; adding a second provider means extending only the facade's
``_client_for`` dispatch). This adapter surfaces the facade's availability
status through the unified capability layer — no behavior change.

Governance pillars honored:
  * Pillar #1 — fully operative real implementation (Twilio via facade).
  * Pillar #2 — call sites use ``TwilioRealtimeAdapter``, not Twilio SDK.
  * Pillar #3 — Twilio credentials are read by the facade from env_2, never raw.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from src.capabilities.base import CapabilityAdapter, CapabilitySpec

logger = logging.getLogger(__name__)


class TwilioRealtimeAdapter(CapabilityAdapter):
    """Telephony / realtime via the existing ``src.telephony.facade``.

    Available when Twilio credentials are configured (TWILIO_ACCOUNT_SID +
    TWILIO_AUTH_TOKEN), as reported by ``telephony_configured()``.
    """

    def _probe(self) -> bool:
        try:
            from src.telephony.facade import telephony_configured
            return telephony_configured()
        except Exception as exc:
            logger.debug("realtime telephony probe failed: %s", exc)
            return False

    def health_probe(self) -> Dict[str, Any]:
        ok = self._probe()
        detail = "unavailable"
        if ok:
            try:
                from src.telephony.facade import telephony_status
                status = telephony_status()
                detail = "available — provider: %s" % status.get("provider", "twilio")
            except Exception:
                detail = "available (Twilio)"
        else:
            detail = ("unavailable (TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not set "
                      "or telephony package missing)")
        return {
            "ok": ok,
            "detail": detail,
            "exec_locus": self.exec_locus,
        }

    def place_call(self, to_number: str, answer_url: str,
                   from_number: str = None, status_callback: str = None,
                   provider: str = None) -> dict:
        """Place an outbound call via the telephony facade."""
        from src.telephony.facade import place_call
        return place_call(
            to_number, answer_url,
            from_number=from_number,
            status_callback=status_callback,
            provider=provider,
        )

    def fetch_call(self, sid: str, provider: str = None) -> dict:
        """Fetch call status from the telephony facade."""
        from src.telephony.facade import fetch_call
        return fetch_call(sid, provider=provider)
