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
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TRACK_QUORUM: Dict[str, float] = {
    "tuning": 0.50,
    "policy": 0.66,
    "safety": 0.75,
}

# Minimum participation floor defaults. A proposal cannot auto-resolve (enact OR
# reject) by quorum until at least this many distinct voters AND this much total
# vote weight have participated — this stops a single unanimous vote from
# instantly meeting a ratio threshold. Both dimensions are operator-configurable
# at runtime via environment / AppSetting (read live, no restart needed), either
# globally or per track:
#   GOVERNANCE_MIN_VOTERS, GOVERNANCE_MIN_VOTERS_<TRACK>
#   GOVERNANCE_MIN_WEIGHT, GOVERNANCE_MIN_WEIGHT_<TRACK>
# A floor of 0 disables that dimension (reverting to the legacy ratio-only check).
DEFAULT_MIN_VOTERS = 2
DEFAULT_MIN_WEIGHT = 0.0


def _env_number(name: str, cast, default):
    """Read a numeric override from the live environment, failing safe.

    Returns ``default`` when the var is unset/blank, and logs + falls back to
    ``default`` when the value is present but unparseable (rather than crashing a
    vote cast on a typo'd setting).
    """
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return cast(raw.strip())
    except (TypeError, ValueError):
        logger.warning(
            "Ignoring malformed governance floor setting %s=%r; using %r",
            name, raw, default,
        )
        return default


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
        participation_weight = yes_w + no_w + abstain_w
        return {
            "yes": round(yes_w, 3),
            "no": round(no_w, 3),
            "abstain": round(abstain_w, 3),
            "total_effective": round(total_effective, 3),
            "participation_weight": round(participation_weight, 3),
            "yes_ratio": round(yes_w / total_effective, 4) if total_effective else 0.0,
            "vote_count": len(votes),
            "delegation_map": delegation_map,
        }

    def participation_floor(self, track: Optional[str] = None) -> Dict[str, float]:
        """Resolve the live minimum-participation floor for a track.

        Reads a per-track override first (e.g. GOVERNANCE_MIN_VOTERS_POLICY),
        then the global setting, then the built-in default. Values are read from
        the process environment on every call so operator changes mirrored into
        env (via settings_store) take effect without a restart. Negative values
        are clamped to 0 (= that dimension disabled).
        """
        track_key = (track or "").strip().upper()

        voters_default = _env_number("GOVERNANCE_MIN_VOTERS", int, DEFAULT_MIN_VOTERS)
        weight_default = _env_number("GOVERNANCE_MIN_WEIGHT", float, DEFAULT_MIN_WEIGHT)

        min_voters = voters_default
        min_weight = weight_default
        if track_key:
            min_voters = _env_number(
                "GOVERNANCE_MIN_VOTERS_%s" % track_key, int, voters_default
            )
            min_weight = _env_number(
                "GOVERNANCE_MIN_WEIGHT_%s" % track_key, float, weight_default
            )

        return {
            "min_voters": max(0, int(min_voters)),
            "min_weight": max(0.0, float(min_weight)),
        }

    def participation_status(
        self, tally: Dict[str, Any], track: Optional[str] = None
    ) -> Dict[str, Any]:
        """Combine a vote tally with the track's floor into a progress view.

        Used by _maybe_enact() to gate auto-resolution and by the dashboard to
        render participation progress alongside the quorum meter.
        """
        floor = self.participation_floor(track)
        min_voters = floor["min_voters"]
        min_weight = floor["min_weight"]
        voters = int(tally.get("vote_count", 0) or 0)
        weight = float(tally.get("participation_weight", 0.0) or 0.0)

        voters_met = voters >= min_voters
        weight_met = weight >= min_weight
        return {
            "min_voters": min_voters,
            "min_weight": round(min_weight, 3),
            "participation_voters": voters,
            "participation_weight": round(weight, 3),
            "voters_met": voters_met,
            "weight_met": weight_met,
            "participation_met": voters_met and weight_met,
            "voters_progress": (
                round(min(voters / min_voters, 1.0), 4) if min_voters > 0 else 1.0
            ),
            "weight_progress": (
                round(min(weight / min_weight, 1.0), 4) if min_weight > 0 else 1.0
            ),
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

        # Minimum participation floor: a single unanimous vote must not be able to
        # instantly meet quorum. Hold the proposal open (no auto-enact AND no
        # auto-reject) until enough distinct voters and total weight participate.
        participation = self.participation_status(tally, prop.track)
        if not participation["participation_met"]:
            logger.info(
                "Proposal %s held open below participation floor "
                "(voters %d/%d, weight %.2f/%.2f)",
                prop.proposal_id,
                participation["participation_voters"], participation["min_voters"],
                participation["participation_weight"], participation["min_weight"],
            )
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
                    "participation": {
                        "voters": participation["participation_voters"],
                        "min_voters": participation["min_voters"],
                        "weight": participation["participation_weight"],
                        "min_weight": participation["min_weight"],
                    },
                }),
            ))
            logger.info(
                "Proposal %s auto-enacted by quorum (yes=%.1f%%, quorum=%.0f%%, "
                "voters=%d, applied=%s)",
                prop.proposal_id, yes_ratio * 100, quorum * 100,
                participation["participation_voters"], applied,
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
        {
            "keywords": ["concurrency cap", "cap concurrency", "reduce concurrency",
                         "limit concurrency", "lower concurrency", "throttle concurrency"],
            "setting_key": "swarm.max_concurrency",
            "op": "set",
            "value": "4",
            "description": "Cap concurrent agent calls per wave to 4 to reduce provider load",
        },
    ]

    def _promote_prompt_version(self, db: Any, prop: Any, action_raw: str) -> List[str]:
        """Promote an evolved prompt version to live within the enactment's txn.

        Triggered by a ``promote_prompt_version:<version_id>`` proposed_action.
        Delegates the validated live/archive flip to the prompt store using the
        SAME db session the enactment commits, so promotion and the
        GovernanceEnactment audit row are atomic. Returns audit tokens.
        """
        try:
            raw_id = action_raw.split("promote_prompt_version:", 1)[1].strip()
            version_id = int(raw_id)
        except Exception:
            logger.warning("Malformed promote_prompt_version action: %r", action_raw)
            return ["prompt_promotion=invalid_action"]
        try:
            from src.promptopt import store as prompt_store
            ok, detail = prompt_store.promote_version_in_session(db, version_id, prop.id)
            if ok:
                logger.info("Prompt version promoted via governance: %s (proposal=%s)",
                            detail, prop.proposal_id)
                return ["promote_prompt_version=%s" % detail]
            logger.warning("Prompt promotion rejected: %s", detail)
            return ["promote_prompt_version=rejected:%s" % detail]
        except Exception as e:
            logger.warning("Prompt promotion failed for version %s: %s", version_id, e)
            return ["promote_prompt_version=error:%s" % e]

    def _apply_enacted_action(self, db: Any, prop: Any) -> List[str]:
        """Parse proposed_action and write bounded setting changes to AppSetting.

        Safety-track proposals are logged but not automatically applied — they
        require a separate operator confirmation step (operator_enact already
        provides that confirmation, so safety track IS applied there).

        Returns a list of "key=value" strings describing what was changed.
        """
        action_raw = (prop.proposed_action or "").strip()
        if action_raw.startswith("promote_prompt_version:"):
            return self._promote_prompt_version(db, prop, action_raw)
        if action_raw.startswith("register_acquired_capability"):
            return self._apply_acquisition_enactment(db, prop, action_raw)

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

    def _apply_acquisition_enactment(self, db: Any, prop: Any, action_raw: str) -> List[str]:
        """Handle enactment of a 'register_acquired_capability' governance proposal.

        Parses the action string (format: 'register_acquired_capability
        capability=X package=Y version=Z registry=W job_id=N'), marks the
        acquisition job and its best candidate as approved, triggers the memory
        siphon to distill a verified claim, and emits a final audit event.
        Never raises — failures return an empty list with a logged warning.
        """
        applied: List[str] = []
        try:
            import re as _re
            def _kv(key: str) -> str:
                m = _re.search(rf"{key}=(\S+)", action_raw)
                return m.group(1) if m else ""

            capability = _kv("capability")
            package = _kv("package")
            version = _kv("version")
            registry = _kv("registry")
            job_id_str = _kv("job_id")
            job_id = int(job_id_str) if job_id_str.isdigit() else None

            if not capability:
                logger.warning(
                    "_apply_acquisition_enactment: missing capability in action: %s", action_raw[:200]
                )
                return ["acquisition_enactment=failed:missing_capability"]

            if job_id:
                from src.models import CapabilityAcquisitionJob, AcquisitionCandidate

                best_cand = (
                    db.query(AcquisitionCandidate)
                    .filter(
                        AcquisitionCandidate.job_id == job_id,
                        AcquisitionCandidate.passed_sandbox == True,
                        AcquisitionCandidate.passed_governance == True,
                    )
                    .first()
                )
                if not best_cand and package:
                    best_cand = (
                        db.query(AcquisitionCandidate)
                        .filter(
                            AcquisitionCandidate.job_id == job_id,
                            AcquisitionCandidate.candidate_name == package,
                        )
                        .first()
                    )
                knowledge_excerpt = best_cand.knowledge_excerpt or "" if best_cand else ""

                # Step 1: Attempt runtime registration FIRST.
                # State and memory distillation are set based on the outcome,
                # so we never record "approved" when the live install failed.
                installed = False
                try:
                    from src.swarm.broker import register_acquired_capability
                    installed = register_acquired_capability(capability, package, version, registry)
                except Exception as reg_exc:
                    logger.warning("register_acquired_capability call failed: %s", reg_exc)

                # Step 2: Update DB state based on runtime registration outcome.
                job = db.query(CapabilityAcquisitionJob).filter(
                    CapabilityAcquisitionJob.id == job_id
                ).first()
                if job:
                    job.state = "approved" if installed else "approved_pending_runtime_install"
                    job.governance_proposal_id = getattr(prop, "proposal_id", None)

                if best_cand:
                    best_cand.passed_governance = True

                db.flush()

                if installed:
                    applied.append(
                        f"acquisition_job_{job_id}=approved capability={capability} "
                        f"package={package}=={version} runtime=installed"
                    )
                    applied.append(f"runtime_registered={capability}→{package}=={version}")
                    logger.info(
                        "_apply_acquisition_enactment: capability '%s' approved and live "
                        "in broker registry via %s==%s (%s)",
                        capability, package, version, registry,
                    )
                else:
                    applied.append(
                        f"acquisition_job_{job_id}=approved_pending_runtime_install "
                        f"capability={capability} package={package}=={version}"
                    )
                    logger.warning(
                        "_apply_acquisition_enactment: pip install into live runtime failed "
                        "for %s==%s; job state=approved_pending_runtime_install; "
                        "capability is governance-approved but not yet broker-dispatched",
                        package, version,
                    )

                # Step 3: Write memory siphon and audit only after confirmed outcome.
                try:
                    from src.acquisition.orchestrator import _siphon_success, _audit
                    if installed:
                        _siphon_success(capability, package, version, registry, knowledge_excerpt)
                        _audit(
                            capability, "approved",
                            f"Governance proposal {getattr(prop, 'proposal_id', '?')} enacted. "
                            f"Package {package}=={version} ({registry}) installed and registered "
                            f"for capability '{capability}'.",
                            target=package,
                        )
                    else:
                        _audit(
                            capability, "approved_pending_runtime_install",
                            f"Governance proposal {getattr(prop, 'proposal_id', '?')} enacted "
                            f"but runtime pip install of {package}=={version} ({registry}) failed. "
                            f"Capability '{capability}' requires manual install to become active.",
                            target=package,
                        )
                except Exception as siphon_exc:
                    logger.warning("Siphon/audit on enactment failed: %s", siphon_exc)
            else:
                logger.warning(
                    "_apply_acquisition_enactment: no job_id in action, cannot update job state: %s",
                    action_raw[:200],
                )
                applied.append(f"acquisition_capability={capability} job_id=unknown")

        except Exception as exc:
            logger.warning("_apply_acquisition_enactment failed: %s", exc)
            return []
        return applied

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
