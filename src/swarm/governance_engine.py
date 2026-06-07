"""
SWARM LAYER: RUNTIME

Governance proposal, voting, delegation, and enactment engine.

OpenGov-inspired in-app voting for swarm self-governance:
- Origins: who raised the proposal (auditor, sentinel, operator, system)
- Tracks: how consequential (tuning < policy < safety)
- Voting: operator cast + optional multi-provider quorum
- Enactment: approved proposals log a GovernanceEnactment; only enacted
  changes take effect. The auditor never silently mutates parameters.

Track quorum thresholds:
  tuning  — >50%  weighted yes to enact (low-impact parameter adjustments)
  policy  — >66%  supermajority (governance rule changes)
  safety  — >75%  for safety/security critical escalations
"""
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TRACK_QUORUM: Dict[str, float] = {
    "tuning": 0.50,
    "policy": 0.66,
    "safety": 0.75,
}


def track_for_meta_claim(category: str, severity: str) -> str:
    """Assign an OpenGov track based on auditor meta-claim category + severity."""
    if severity == "critical":
        return "safety"
    if category in ("drift", "anomaly") and severity == "warning":
        return "policy"
    return "tuning"


class GovernanceEngine:
    """Converts MetaClaims/SentinelAlerts into typed governance proposals."""

    def raise_proposal(
        self,
        origin: str,
        track: str,
        title: str,
        description: str,
        proposed_action: str,
        evidence: List[str],
        source_claim_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Persist a new governance proposal. Returns the proposal_id string, or
        None if persistence failed (caller should log but not hard-fail).
        """
        try:
            from src.database import SessionLocal
            from src.models import GovernanceProposal
            db = SessionLocal()
            try:
                proposal_id = f"GOV-{uuid.uuid4().hex[:8].upper()}"
                p = GovernanceProposal(
                    proposal_id=proposal_id,
                    origin=origin[:50],
                    track=track[:20],
                    title=title[:200],
                    description=description[:1000],
                    proposed_action=proposed_action[:500],
                    evidence_json=json.dumps(evidence[:10]),
                    quorum_threshold=TRACK_QUORUM.get(track, 0.50),
                    status="open",
                    source_claim_id=(source_claim_id or "")[:50],
                )
                db.add(p)
                db.commit()
                logger.info(
                    "Governance proposal raised: %s [origin=%s track=%s]",
                    proposal_id, origin, track,
                )
                return proposal_id
            except Exception as e:
                logger.warning("Failed to persist governance proposal: %s", e)
                db.rollback()
                return None
            finally:
                db.close()
        except Exception as e:
            logger.warning("GovernanceEngine.raise_proposal error: %s", e)
            return None

    def cast_vote(
        self,
        proposal_db_id: int,
        voter: str,
        vote: str,
        weight: float = 1.0,
        reason: str = "",
    ) -> bool:
        """
        Cast or update a vote on an open proposal.

        vote must be one of: "yes", "no", "abstain", "delegated"
        Returns True if persisted successfully.
        """
        try:
            from src.database import SessionLocal
            from src.models import GovernanceProposal, GovernanceVote
            if vote not in ("yes", "no", "abstain", "delegated"):
                return False
            db = SessionLocal()
            try:
                prop = db.query(GovernanceProposal).filter(
                    GovernanceProposal.id == proposal_db_id
                ).first()
                if not prop or prop.status != "open":
                    return False
                existing = db.query(GovernanceVote).filter(
                    GovernanceVote.proposal_id == prop.id,
                    GovernanceVote.voter == voter,
                ).first()
                if existing:
                    existing.vote = vote
                    existing.weight = weight
                    existing.reason = reason[:500]
                else:
                    db.add(GovernanceVote(
                        proposal_id=prop.id,
                        voter=voter[:100],
                        vote=vote,
                        weight=max(0.0, float(weight)),
                        reason=(reason or "")[:500],
                    ))
                db.flush()
                self._maybe_enact(db, prop)
                db.commit()
                return True
            except Exception as e:
                logger.warning("Vote cast failed: %s", e)
                db.rollback()
                return False
            finally:
                db.close()
        except Exception as e:
            logger.warning("GovernanceEngine.cast_vote error: %s", e)
            return False

    def delegate(self, proposal_db_id: int, delegator: str, delegate_to: str) -> bool:
        """Record a delegation: delegator hands their vote weight to delegate_to.

        The delegation is stored as vote="delegated" with reason="delegated_to:<voter>".
        Tally resolution in _resolve_vote_tally() follows delegation chains and
        transfers the delegator's weight to the delegate's effective vote direction.
        """
        return self.cast_vote(
            proposal_db_id=proposal_db_id,
            voter=delegator,
            vote="delegated",
            weight=1.0,
            reason=f"delegated_to:{delegate_to}",
        )

    def operator_enact(self, proposal_db_id: int, operator: str = "admin") -> bool:
        """Operator directly enacts an open proposal (overrides quorum for urgent cases).

        After updating status, calls _apply_enacted_action() to apply bounded,
        validated parameter updates to the AppSetting store so the change
        actually takes effect in subsequent swarm runs.
        """
        try:
            from src.database import SessionLocal
            from src.models import GovernanceProposal, GovernanceEnactment
            db = SessionLocal()
            try:
                prop = db.query(GovernanceProposal).filter(
                    GovernanceProposal.id == proposal_db_id
                ).first()
                if not prop or prop.status != "open":
                    return False
                prop.status = "enacted"
                prop.enacted_at = datetime.utcnow()
                applied = self._apply_enacted_action(db, prop)
                change_meta = {
                    "action": prop.proposed_action,
                    "enacted_by": operator,
                    "track": prop.track,
                    "method": "operator_direct",
                    "applied_settings": applied,
                }
                db.add(GovernanceEnactment(
                    proposal_id=prop.id,
                    enacted_by=(operator or "admin")[:100],
                    change_json=json.dumps(change_meta),
                ))
                db.commit()
                logger.info("Proposal %d enacted by operator %s; applied: %s", proposal_db_id, operator, applied)
                return True
            except Exception as e:
                logger.warning("Operator enact failed: %s", e)
                db.rollback()
                return False
            finally:
                db.close()
        except Exception as e:
            logger.warning("GovernanceEngine.operator_enact error: %s", e)
            return False

    def operator_reject(self, proposal_db_id: int) -> bool:
        """Operator rejects an open proposal."""
        try:
            from src.database import SessionLocal
            from src.models import GovernanceProposal
            db = SessionLocal()
            try:
                prop = db.query(GovernanceProposal).filter(
                    GovernanceProposal.id == proposal_db_id
                ).first()
                if not prop or prop.status != "open":
                    return False
                prop.status = "rejected"
                db.commit()
                return True
            except Exception as e:
                logger.warning("Operator reject failed: %s", e)
                db.rollback()
                return False
            finally:
                db.close()
        except Exception as e:
            return False

    def _resolve_vote_tally(self, db: Any, proposal_id: int) -> Dict[str, float]:
        """Resolve delegation chains and return effective yes/no/abstain weights.

        Algorithm:
        1. Collect all votes for this proposal.
        2. Build a delegation map: voter → delegate_to (from reason="delegated_to:<X>").
        3. For each delegated vote, follow the chain to a terminal voter (who cast
           yes/no/abstain). If the chain reaches a voter with no terminal vote,
           the delegated weight is treated as abstain.
        4. Return totals: {"yes": w, "no": w, "abstain": w, "total": w,
           "delegation_map": {delegator: delegate_to, ...}}.

        Protections: max chain depth of 10 to prevent infinite loops.
        """
        from src.models import GovernanceVote
        votes = db.query(GovernanceVote).filter(
            GovernanceVote.proposal_id == proposal_id,
        ).all()

        by_voter: Dict[str, Any] = {v.voter: v for v in votes}
        delegation_map: Dict[str, str] = {}

        for v in votes:
            if v.vote == "delegated" and v.reason and v.reason.startswith("delegated_to:"):
                target = v.reason.split("delegated_to:", 1)[1].strip()
                delegation_map[v.voter] = target

        def resolve(voter: str, depth: int = 0) -> str:
            """Walk delegation chain to find the terminal vote direction."""
            if depth > 10:
                return "abstain"
            v = by_voter.get(voter)
            if v is None:
                return "abstain"
            if v.vote in ("yes", "no", "abstain"):
                return v.vote
            if v.vote == "delegated" and voter in delegation_map:
                return resolve(delegation_map[voter], depth + 1)
            return "abstain"

        yes_w = no_w = abstain_w = 0.0
        for v in votes:
            direction = resolve(v.voter)
            w = v.weight
            if direction == "yes":
                yes_w += w
            elif direction == "no":
                no_w += w
            else:
                abstain_w += w

        total_effective = yes_w + no_w
        return {
            "yes": round(yes_w, 3),
            "no": round(no_w, 3),
            "abstain": round(abstain_w, 3),
            "total_effective": round(total_effective, 3),
            "yes_ratio": round(yes_w / total_effective, 4) if total_effective else 0.0,
            "vote_count": len(votes),
            "delegation_map": delegation_map,
        }

    def _maybe_enact(self, db: Any, prop: Any) -> None:
        """Check quorum after a vote and auto-enact/reject if threshold is reached.

        Uses _resolve_vote_tally() which follows delegation chains so delegated
        vote weight is properly counted in the effective yes/no totals.
        Safety-track proposals never auto-enact; they require explicit operator action.
        """
        from src.models import GovernanceEnactment
        tally = self._resolve_vote_tally(db, prop.id)
        total = tally["total_effective"]
        if total == 0:
            return
        yes_ratio = tally["yes_ratio"]
        no_ratio = tally["no"] / total if total else 0.0
        quorum = prop.quorum_threshold or 0.50

        if yes_ratio >= quorum and prop.track != "safety":
            prop.status = "enacted"
            prop.enacted_at = datetime.utcnow()
            applied = self._apply_enacted_action(db, prop)
            db.add(GovernanceEnactment(
                proposal_id=prop.id,
                enacted_by="quorum",
                change_json=json.dumps({
                    "action": prop.proposed_action,
                    "yes_ratio": round(yes_ratio, 3),
                    "total_effective": round(total, 2),
                    "track": prop.track,
                    "method": "quorum",
                    "applied_settings": applied,
                    "delegation_map": tally["delegation_map"],
                }),
            ))
            logger.info(
                "Proposal %s auto-enacted by quorum (yes=%.1f%%, quorum=%.0f%%, applied=%s)",
                prop.proposal_id, yes_ratio * 100, quorum * 100, applied,
            )
        elif no_ratio > (1.0 - quorum):
            prop.status = "rejected"

    # -------------------------------------------------------------------------
    # Enactment applier — bounded, validated parameter writes to AppSetting
    # -------------------------------------------------------------------------

    # Keywords that appear in proposed_action text, mapped to (setting_key, delta).
    # All numeric deltas are clamped to safe ranges before being written.
    _ACTION_PATTERNS: List[Dict] = [
        {
            "keywords": ["increase evidence", "evidence requirements"],
            "setting_key": "swarm.min_evidence_strength",
            "op": "set",
            "value": "0.7",
            "description": "Raise minimum evidence_strength threshold for VERIFIED claims to 0.7",
        },
        {
            "keywords": ["halt pipeline", "halt synthesis", "halting synthesis"],
            "setting_key": "swarm.halt_on_critical",
            "op": "set",
            "value": "true",
            "description": "Stop the swarm pipeline immediately on the next critical HR/CS breach",
        },
        {
            "keywords": ["compliance correction", "cs degradation"],
            "setting_key": "swarm.min_cs_threshold",
            "op": "set",
            "value": "50",
            "description": "Raise minimum per-agent compliance score threshold to 50",
        },
        {
            "keywords": ["quarantine", "redistribute load"],
            "setting_key": "swarm.quarantine_flagged_providers",
            "op": "set",
            "value": "true",
            "description": "Enable automatic quarantine of providers with 3+ consecutive failures",
        },
        {
            "keywords": ["debate round", "additional debate", "review dissenting"],
            "setting_key": "swarm.extra_debate_rounds",
            "op": "increment",
            "value": "1",
            "description": "Add one extra debate round on dissent surge",
        },
        {
            "keywords": ["skeptic agent", "add skeptic"],
            "setting_key": "swarm.enable_skeptic_override",
            "op": "set",
            "value": "true",
            "description": "Force a dedicated skeptic pass on the next wave",
        },
    ]

    def _apply_enacted_action(self, db: Any, prop: Any) -> List[str]:
        """Parse proposed_action and write bounded setting changes to AppSetting.

        Safety-track proposals are logged but not automatically applied — they
        require a separate operator confirmation step (operator_enact already
        provides that confirmation, so safety track IS applied there).

        Returns a list of "key=value" strings describing what was changed.
        """
        try:
            from src.models import AppSetting
            applied: List[str] = []
            action_lower = (prop.proposed_action or "").lower()

            for pattern in self._ACTION_PATTERNS:
                if any(kw in action_lower for kw in pattern["keywords"]):
                    key = pattern["setting_key"]
                    value = pattern["value"]

                    setting = db.query(AppSetting).filter(AppSetting.key == key).first()
                    if pattern["op"] == "increment":
                        current = int(setting.value or "0") if setting else 0
                        new_val = str(min(current + int(value), 5))
                    else:
                        new_val = value

                    if setting:
                        setting.value = new_val
                    else:
                        db.add(AppSetting(key=key, value=new_val))

                    applied.append(f"{key}={new_val}")
                    logger.info(
                        "Enacted action applied: proposal=%s key=%s value=%s",
                        prop.proposal_id, key, new_val,
                    )

            if not applied:
                # Record the free-text action as a pending operator note
                key = f"swarm.pending_action.{prop.proposal_id}"
                note = (prop.proposed_action or "no action specified")[:500]
                setting = db.query(AppSetting).filter(AppSetting.key == key).first()
                if setting:
                    setting.value = note
                else:
                    db.add(AppSetting(key=key, value=note))
                applied.append(f"{key}=<pending_note>")
                logger.info("No mapped action pattern; recorded as pending note: %s", key)

            return applied
        except Exception as e:
            logger.warning("_apply_enacted_action failed for proposal %s: %s", getattr(prop, "proposal_id", "?"), e)
            return []

    @classmethod
    def from_meta_claim(cls, claim: Any) -> Optional[str]:
        """Convenience: raise a governance proposal from a MetaClaim dataclass."""
        engine = cls()
        track = track_for_meta_claim(claim.category, claim.severity)
        return engine.raise_proposal(
            origin="auditor",
            track=track,
            title=claim.description[:120],
            description=claim.description,
            proposed_action=claim.proposed_action,
            evidence=list(claim.evidence or []),
            source_claim_id=claim.claim_id,
        )
