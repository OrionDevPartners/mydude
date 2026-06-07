"""
SWARM LAYER: RUNTIME

GovernanceSettings — reads enacted AppSetting keys from the database and
exposes a typed API so the orchestrator, sentinel, and compliance paths can
query them at run-start without coupling those modules to the DB directly.

Enacted settings (written by GovernanceEngine._apply_enacted_action):
  swarm.halt_on_critical          bool   — abort pipeline on sentinel critical escalation
  swarm.min_cs_threshold          int    — minimum per-agent CS to accept output
  swarm.min_evidence_strength     float  — minimum evidence_strength for VERIFIED claims
  swarm.quarantine_flagged_providers bool — auto-quarantine providers with 3+ failures
  swarm.extra_debate_rounds       int    — extra debate rounds appended per wave
  swarm.enable_skeptic_override   bool   — force a dedicated SKEPTIC pass next wave

Usage:
  gs = GovernanceSettings.load()
  if gs.halt_on_critical:
      ...
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_MIN_CS = 30       # matches orchestrator's current hard-coded threshold
_DEFAULT_MIN_EVIDENCE = 0.5
_DEFAULT_EXTRA_ROUNDS = 0


@dataclass
class GovernanceSettings:
    halt_on_critical: bool = False
    min_cs_threshold: int = _DEFAULT_MIN_CS
    min_evidence_strength: float = _DEFAULT_MIN_EVIDENCE
    quarantine_flagged_providers: bool = False
    extra_debate_rounds: int = _DEFAULT_EXTRA_ROUNDS
    enable_skeptic_override: bool = False

    @classmethod
    def load(cls) -> "GovernanceSettings":
        """Read enacted AppSetting keys from the DB. Returns defaults on any failure."""
        try:
            from src.database import SessionLocal
            from src.models import AppSetting
            db = SessionLocal()
            try:
                rows = db.query(AppSetting).filter(
                    AppSetting.key.like("swarm.%")
                ).all()
                settings_map = {r.key: r.value for r in rows}
            finally:
                db.close()

            def _bool(key: str, default: bool) -> bool:
                v = settings_map.get(key, "").strip().lower()
                if v in ("1", "true", "yes"):
                    return True
                if v in ("0", "false", "no"):
                    return False
                return default

            def _int(key: str, default: int) -> int:
                try:
                    return int(settings_map[key])
                except (KeyError, ValueError, TypeError):
                    return default

            def _float(key: str, default: float) -> float:
                try:
                    return float(settings_map[key])
                except (KeyError, ValueError, TypeError):
                    return default

            gs = cls(
                halt_on_critical=_bool("swarm.halt_on_critical", False),
                min_cs_threshold=min(max(_int("swarm.min_cs_threshold", _DEFAULT_MIN_CS), 0), 100),
                min_evidence_strength=min(max(_float("swarm.min_evidence_strength", _DEFAULT_MIN_EVIDENCE), 0.0), 1.0),
                quarantine_flagged_providers=_bool("swarm.quarantine_flagged_providers", False),
                extra_debate_rounds=min(max(_int("swarm.extra_debate_rounds", 0), 0), 5),
                enable_skeptic_override=_bool("swarm.enable_skeptic_override", False),
            )
            logger.debug(
                "GovernanceSettings loaded: halt=%s min_cs=%d min_ev=%.2f quarantine=%s extra_rounds=%d skeptic=%s",
                gs.halt_on_critical, gs.min_cs_threshold, gs.min_evidence_strength,
                gs.quarantine_flagged_providers, gs.extra_debate_rounds, gs.enable_skeptic_override,
            )
            return gs
        except Exception as e:
            logger.warning("GovernanceSettings.load() failed, using defaults: %s", e)
            return cls()
