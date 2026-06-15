"""Unified vendor-agnostic capability adapter contract.

Every capability category (llm, browser, database, vector_search, …) registers
adapters that implement ``CapabilityAdapter``. Calling code resolves adapters
through the ``CapabilityResolver`` and never names a vendor or category
implementation directly.

Governance pillars honored:
  * Pillar #1 — every concrete adapter is a fully-operative real implementation.
  * Pillar #2 — call sites name a *category*, never a vendor.
  * Pillar #3 — secrets are sourced by NAME from env_2 (secrets.py), never raw.
  * Pillar #4 — availability gating is explicit; unavailability is observable.
  * Pillar #6 — forward-compatible agnostic interface, evolvable without churn.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class CapabilitySpec:
    """A capability provider/backend definition loaded from env_1
    (config/providers.toml). Generic across all categories."""

    key: str
    adapter: str
    category: str
    secrets: List[str] = field(default_factory=list)
    exec_locus: str = "local"
    label: str = ""
    notes: str = ""
    cost: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_keyless(self) -> bool:
        """True when this adapter needs no secrets (local/built-in)."""
        return not self.secrets


class CapabilityAdapter(ABC):
    """Vendor-agnostic capability adapter contract.

    Concrete adapters implement this for each (category, adapter) pair.
    Callers resolve adapters through :class:`~src.capabilities.resolver.CapabilityResolver`
    and invoke them via category-specific helper functions — never by
    instantiating vendor classes directly.
    """

    def __init__(self, spec: CapabilitySpec) -> None:
        self.spec = spec

    # -- identity / config ---------------------------------------------------

    @property
    def key(self) -> str:
        return self.spec.key

    @property
    def category(self) -> str:
        return self.spec.category

    @property
    def exec_locus(self) -> str:
        return self.spec.exec_locus

    @property
    def label(self) -> str:
        return self.spec.label or self.spec.key

    # -- availability --------------------------------------------------------

    def secrets_present(self) -> bool:
        """True if every required env_2 secret for this adapter is set."""
        if self.spec.is_keyless:
            return True
        from src.providers.secrets import has_secret
        return all(has_secret(s) for s in self.spec.secrets)

    def is_available(self) -> bool:
        """True if this adapter can currently serve requests.

        Must NOT raise — it drives provider selection and the status UI.
        Default: secrets present and the backend-specific probe passes.
        Subclasses override ``_probe()`` for cheap liveness checks.
        """
        try:
            return self.secrets_present() and self._probe()
        except Exception:
            return False

    def _probe(self) -> bool:
        """Backend-specific liveness probe. Override in subclasses.

        Should be cheap (TCP connect, import check, …). Default: True.
        """
        return True

    def health_probe(self) -> Dict[str, Any]:
        """Return a structured health status dict for the capability matrix UI.

        Returns ``{"ok": bool, "detail": str, "exec_locus": str}``.
        Never raises — a failed probe returns ``ok: False`` with the error.
        """
        try:
            ok = self.is_available()
            return {
                "ok": ok,
                "detail": "available" if ok else "unavailable",
                "exec_locus": self.exec_locus,
            }
        except Exception as exc:
            return {"ok": False, "detail": str(exc), "exec_locus": self.exec_locus}

    # -- secret names (for handshake / lint) ----------------------------------

    @property
    def required_secrets(self) -> List[str]:
        return list(self.spec.secrets)

    # -- optional: jurisdiction / policy passthrough -------------------------

    def jurisdiction_allowed(self, exec_locus_pin: Optional[str] = None,
                             cloud_shift: Optional[bool] = None) -> bool:
        """True if this adapter's exec_locus is permitted under current policy.

        Mirrors the LLM/browser jurisdiction checks so every category respects
        the cloud-shift kill switch and exec_locus_pin setting.
        """
        try:
            from src.swarm.jurisdiction import get_exec_locus_pin, get_cloud_shift
            pin = exec_locus_pin or get_exec_locus_pin()
            shift = cloud_shift if cloud_shift is not None else get_cloud_shift()
        except Exception:
            return True  # no jurisdiction layer — default permissive

        locus = self.exec_locus

        # cloud_shift=False means only local execution is permitted.
        if not shift and locus not in ("local",):
            return False

        # exec_locus_pin restricts to an exact locus.
        if pin and pin != locus:
            # "local" pin also permits adapters already marked local.
            if pin == "local" and locus == "local":
                return True
            return False

        return True
