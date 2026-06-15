"""Capability drift detector — declared vs. actual import/call graph.

Diffs the *declared* capability map (capability_contracts.all_contracts())
against the *actual* dispatch table extracted from broker.py's AST, surfacing:

  orphaned_declarations  — declared in contracts but no matching broker handler
  undeclared_handlers    — broker handles the capability but no contract declared

The delta is surfaced as an operator-visible report in the Governance Center.
Nothing here auto-patches source or manifests (governance pillar #1: no stubs).

Report persistence: ``.devguard/drift_report.json`` — created on first scan,
updated on each subsequent call to ``compute_and_persist()``.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Report structures
# --------------------------------------------------------------------------- #

@dataclass
class DriftEntry:
    capability: str
    reason: str
    severity: str = "info"


@dataclass
class PreconditionGapEntry:
    """A capability whose broker handler makes outbound integration calls
    but whose contract declares zero enforced_preconditions — i.e. the
    handler has side-effecting outbound calls with no contract-level safety
    validators.  Surfaced separately from orphaned/undeclared so operators
    can prioritise governance hardening.
    """
    capability: str
    integration_calls: List[str]
    precondition_count: int
    severity: str = "warning"

    @property
    def reason(self) -> str:
        calls_str = ", ".join(self.integration_calls) or "unknown"
        return (
            f"Handler calls [{calls_str}] but contract has "
            f"{self.precondition_count} enforced precondition(s)."
        )


@dataclass
class DriftReport:
    scanned_at: str
    declared_count: int
    handled_count: int
    orphaned: List[DriftEntry] = field(default_factory=list)
    undeclared: List[DriftEntry] = field(default_factory=list)
    precondition_gaps: List[PreconditionGapEntry] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def orphaned_count(self) -> int:
        return len(self.orphaned)

    @property
    def undeclared_count(self) -> int:
        return len(self.undeclared)

    @property
    def precondition_gap_count(self) -> int:
        return len(self.precondition_gaps)

    @property
    def total_drift(self) -> int:
        return self.orphaned_count + self.undeclared_count + self.precondition_gap_count

    def to_dict(self) -> dict:
        d = asdict(self)
        d["precondition_gaps"] = [
            {
                "capability": g.capability,
                "integration_calls": g.integration_calls,
                "precondition_count": g.precondition_count,
                "severity": g.severity,
                "reason": g.reason,
            }
            for g in self.precondition_gaps
        ]
        d["orphaned_count"] = self.orphaned_count
        d["undeclared_count"] = self.undeclared_count
        d["precondition_gap_count"] = self.precondition_gap_count
        d["total_drift"] = self.total_drift
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "DriftReport":
        orphaned = [DriftEntry(**e) for e in (d.get("orphaned") or [])]
        undeclared = [DriftEntry(**e) for e in (d.get("undeclared") or [])]
        gaps_raw = d.get("precondition_gaps") or []
        precondition_gaps = [
            PreconditionGapEntry(
                capability=g.get("capability", ""),
                integration_calls=g.get("integration_calls", []),
                precondition_count=g.get("precondition_count", 0),
                severity=g.get("severity", "warning"),
            )
            for g in gaps_raw
        ]
        return cls(
            scanned_at=d.get("scanned_at", ""),
            declared_count=d.get("declared_count", 0),
            handled_count=d.get("handled_count", 0),
            orphaned=orphaned,
            undeclared=undeclared,
            precondition_gaps=precondition_gaps,
            error=d.get("error"),
        )

    @classmethod
    def empty(cls) -> "DriftReport":
        return cls(
            scanned_at=_now(),
            declared_count=0,
            handled_count=0,
        )


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #

def _report_path() -> Path:
    env = os.environ.get("DEVGUARD_DRIFT_REPORT_PATH")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    repo_root = here.parents[2]
    data_dir = repo_root / ".devguard"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "drift_report.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def persist_report(report: DriftReport) -> None:
    """Write report to .devguard/drift_report.json (fail-soft)."""
    try:
        path = _report_path()
        path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("drift_detector: failed to persist report: %s", exc)


def load_last_report() -> Optional[DriftReport]:
    """Load the last persisted drift report, or None if none exists."""
    try:
        path = _report_path()
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return DriftReport.from_dict(data)
    except Exception as exc:
        logger.debug("drift_detector: failed to load report: %s", exc)
        return None


# --------------------------------------------------------------------------- #
# Core diff logic
# --------------------------------------------------------------------------- #

def _compute_precondition_gaps(
    handled_info: Dict,
    contracts_by_name: Dict,
) -> List[PreconditionGapEntry]:
    """Detect capabilities whose broker handler makes outbound integration calls
    but whose contract declares zero enforced_preconditions.

    This is the import/call-graph drift dimension: a handler that calls
    ``self.integrations.X()`` with no contract-level validators is a latent
    governance gap — side-effecting outbound calls without safety preconditions.

    Parameters
    ----------
    handled_info : {cap_name: BrokerHandlerInfo} — from extract_broker_handlers.
    contracts_by_name : {cap_name: CapabilityContract} — from all_contracts().
    """
    gaps: List[PreconditionGapEntry] = []
    for cap_name, info in handled_info.items():
        integration_calls = getattr(info, "calls_made", [])
        if not integration_calls:
            continue
        contract = contracts_by_name.get(cap_name)
        if contract is None:
            continue
        n_preconditions = len(getattr(contract, "enforced_preconditions", []))
        if n_preconditions == 0:
            gaps.append(PreconditionGapEntry(
                capability=cap_name,
                integration_calls=integration_calls,
                precondition_count=0,
                severity="warning",
            ))
    return gaps


def _get_handled_handler_info() -> Dict:
    """All capability handler infos (with per-branch call lists) from broker.py AST."""
    try:
        from src.swarm.ast_router import extract_broker_handlers
        broker_path = Path(__file__).resolve().parent / "broker.py"
        return extract_broker_handlers(broker_path)
    except Exception as exc:
        logger.warning("drift_detector: broker handler extraction failed: %s", exc)
        return {}


def _get_contracts_by_name() -> Dict:
    """Dict of {capability_name: CapabilityContract} from all_contracts()."""
    try:
        from src.swarm.capability_contracts import all_contracts
        return {c.capability: c for c in all_contracts()}
    except Exception as exc:
        logger.warning("drift_detector: failed to load contracts: %s", exc)
        return {}


def compute_drift() -> DriftReport:
    """Compute the current declared-vs-actual drift for capabilities.

    Scans broker.py AST for handled capabilities, compares against declared
    contracts, and performs call-graph precondition-gap analysis. Returns a
    DriftReport — never raises.

    Three drift dimensions:
    1. orphaned_declarations — declared contracts with no broker handler.
    2. undeclared_handlers — broker handles the capability but no contract.
    3. precondition_gaps — handler calls self.integrations.X() but the
       contract declares zero enforced_preconditions (call-graph drift).
    """
    try:
        contracts_by_name = _get_contracts_by_name()
        declared = set(contracts_by_name.keys())
        handled_info = _get_handled_handler_info()
        handled = set(handled_info.keys())

        _BLOCKED = {"read_secret_raw", "dump_vault", "export_all_secrets"}
        orphaned_names = declared - handled - _BLOCKED
        undeclared_names = handled - declared

        orphaned = [
            DriftEntry(
                capability=name,
                reason="Declared in capability_contracts but no broker handler found.",
                severity="warning",
            )
            for name in sorted(orphaned_names)
        ]
        undeclared = [
            DriftEntry(
                capability=name,
                reason="Broker handles this capability but no contract declared.",
                severity="warning",
            )
            for name in sorted(undeclared_names)
        ]

        precondition_gaps = _compute_precondition_gaps(handled_info, contracts_by_name)
        precondition_gaps.sort(key=lambda g: g.capability)

        return DriftReport(
            scanned_at=_now(),
            declared_count=len(declared),
            handled_count=len(handled),
            orphaned=orphaned,
            undeclared=undeclared,
            precondition_gaps=precondition_gaps,
        )
    except Exception as exc:
        logger.warning("drift_detector: compute_drift failed: %s", exc)
        return DriftReport(
            scanned_at=_now(),
            declared_count=0,
            handled_count=0,
            error=str(exc),
        )


def compute_and_persist() -> DriftReport:
    """Compute and persist the drift report. Returns the report."""
    report = compute_drift()
    persist_report(report)
    if report.total_drift > 0:
        logger.info(
            "drift_detector: %d orphaned declarations, %d undeclared handlers",
            report.orphaned_count,
            report.undeclared_count,
        )
    return report


def get_or_refresh(*, max_age_seconds: float = 300.0) -> DriftReport:
    """Return the cached report if fresh, else recompute and persist.

    ``max_age_seconds`` controls how long the cached report is trusted
    (default: 5 minutes). On any error, falls back to loading the last
    persisted report, then to an empty report.
    """
    import time
    try:
        existing = load_last_report()
        if existing is not None and existing.scanned_at:
            from datetime import timezone as _tz
            scanned = datetime.fromisoformat(existing.scanned_at.replace("Z", "+00:00"))
            age = (datetime.now(_tz.utc) - scanned).total_seconds()
            if age < max_age_seconds:
                return existing
    except Exception:
        pass

    try:
        return compute_and_persist()
    except Exception as exc:
        logger.warning("drift_detector: refresh failed: %s", exc)
        last = load_last_report()
        return last if last is not None else DriftReport.empty()
