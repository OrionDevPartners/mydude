from datetime import datetime
from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, Boolean, DateTime, Float, JSON
)
from src.database import Base


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    priority = Column(String(20), default="medium")
    status = Column(String(20), default="pending")
    category = Column(String(100), nullable=True)
    due_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Note(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=True)
    category = Column(String(100), nullable=True)
    tags = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CommandLog(Base):
    __tablename__ = "command_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    command = Column(String(500), nullable=False)
    output = Column(Text, nullable=True)
    status = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserSettings(Base):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, unique=True, nullable=False, index=True)
    authorized = Column(Boolean, default=False)
    timezone = Column(String(50), default="UTC")
    asana_project_gid = Column(String, nullable=True)
    linear_token = Column(String, nullable=True)
    github_token = Column(String, nullable=True)
    slack_webhook = Column(String, nullable=True)
    discord_webhook = Column(String, nullable=True)
    digest_enabled = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    command = Column(String(100), nullable=False)
    args = Column(Text, nullable=True)
    status = Column(String(20), default="ok")
    output_preview = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ConversationMemory(Base):
    __tablename__ = "conversation_memory"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    source = Column(String(50), nullable=False)
    content = Column(Text, nullable=False)
    summary = Column(Text, nullable=True)
    entities = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Goal(Base):
    __tablename__ = "goals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    objective = Column(Text, nullable=False)
    status = Column(String(30), default="active")
    progress_pct = Column(Integer, default=0)
    last_result = Column(Text, nullable=True)
    wave_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CronJob(Base):
    __tablename__ = "cron_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    schedule = Column(String(100), nullable=False)
    command = Column(String(500), nullable=False)
    description = Column(String(255), nullable=True)
    enabled = Column(Boolean, default=True)
    last_run = Column(DateTime, nullable=True)
    next_run = Column(DateTime, nullable=True)
    last_output = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ProviderMetric(Base):
    __tablename__ = "provider_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(30), nullable=False)
    model = Column(String(100), nullable=False)
    prompt_type = Column(String(50), nullable=False)
    latency_ms = Column(Integer, nullable=False)
    success = Column(Boolean, nullable=False)
    token_count = Column(Integer, nullable=True)
    rating = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class IntegrationConfig(Base):
    __tablename__ = "integration_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    provider = Column(String(50), nullable=False)
    config_json = Column(Text, nullable=True)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PipelineTrigger(Base):
    __tablename__ = "pipeline_triggers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    trigger_command = Column(String(100), nullable=False)
    actions_json = Column(Text, nullable=False)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class DigestConfig(Base):
    __tablename__ = "digest_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, unique=True, nullable=False, index=True)
    frequency = Column(String(20), default="daily")
    hour_utc = Column(Integer, default=9)
    day_of_week = Column(Integer, default=1)
    enabled = Column(Boolean, default=True)
    last_sent = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class SwarmMemoryLayer(Base):
    __tablename__ = "swarm_memory_layers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    layer_type = Column(String(30), nullable=False)
    content = Column(Text, nullable=False)
    summary = Column(Text, nullable=True)
    topic = Column(String(255), nullable=True)
    compliance_score = Column(Integer, default=100)
    hallucination_risk = Column(Float, default=0.0)
    access_count = Column(Integer, default=0)
    decay_factor = Column(Float, default=1.0)
    session_id = Column(String(100), nullable=True)
    wave_idx = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_accessed = Column(DateTime, nullable=True)


class ClaimProvenanceRecord(Base):
    __tablename__ = "claim_provenance"

    id = Column(Integer, primary_key=True, autoincrement=True)
    claim_id = Column(String(50), nullable=False, index=True)
    origin_provider = Column(String(30), nullable=True)
    origin_role = Column(String(50), nullable=True)
    wave_idx = Column(Integer, nullable=True)
    claim_text = Column(Text, nullable=True)
    evidence_json = Column(Text, nullable=True)
    parent_claim_ids = Column(Text, nullable=True)
    hr_at_creation = Column(Float, default=0.0)
    cs_at_creation = Column(Integer, default=100)
    transformations_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PerformanceLedgerEntry(Base):
    __tablename__ = "performance_ledger"

    id = Column(Integer, primary_key=True, autoincrement=True)
    wave_idx = Column(Integer, nullable=False)
    avg_cs = Column(Float, nullable=False)
    avg_hr = Column(Float, nullable=False)
    agent_count = Column(Integer, default=0)
    consensus_confidence = Column(Float, default=0.0)
    dissent_count = Column(Integer, default=0)
    meta_claims_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class SentinelEvent(Base):
    __tablename__ = "sentinel_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    alert_id = Column(String(50), nullable=False, index=True)
    alert_type = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False)
    description = Column(Text, nullable=True)
    recommended_action = Column(Text, nullable=True)
    acknowledged = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(50), nullable=False)
    label = Column(String(100), nullable=True)
    encrypted_key = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True)
    category = Column(String(60), nullable=True)
    env_var = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    rotation_days = Column(Integer, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    last_rotated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class KeyAuditLog(Base):
    __tablename__ = "key_audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    api_key_id = Column(Integer, nullable=True, index=True)
    provider = Column(String(50), nullable=True)
    label = Column(String(100), nullable=True)
    action = Column(String(40), nullable=False)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class CapabilityAuditLog(Base):
    __tablename__ = "capability_audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    capability = Column(String(60), nullable=False, index=True)
    target = Column(Text, nullable=True)
    backend = Column(String(60), nullable=True)
    status = Column(String(20), nullable=False, default="ok")
    detail = Column(Text, nullable=True)
    source = Column(String(40), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class TaskRun(Base):
    __tablename__ = "task_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    prompt = Column(Text, nullable=False)
    result = Column(Text, nullable=True)
    status = Column(String(30), default="pending")
    provider_scores = Column(Text, nullable=True)
    execution_time_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Subscription(Base):
    """A tracked recurring subscription.

    Discovery produces ``candidate`` rows (inferred from reachable signals); the
    user confirms them into ``confirmed`` before MyDude will ever act on them.
    The login secret (password) lives encrypted in the credential vault and is
    referenced here by ``credential_key_id`` — never stored in plaintext on this
    row. The account identifier (``login_username``, often an email) is stored
    here so the login flow can fill it.
    """
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), nullable=False)
    domain = Column(String(255), nullable=True)
    login_url = Column(Text, nullable=True)
    account_url = Column(Text, nullable=True)
    login_username = Column(String(255), nullable=True)
    # FK (logical) to api_keys.id holding the encrypted account password.
    credential_key_id = Column(Integer, nullable=True, index=True)
    # candidate | confirmed | dismissed | cancel_pending | cancelled
    status = Column(String(30), default="candidate", index=True)
    est_cost = Column(String(60), nullable=True)
    currency = Column(String(10), nullable=True)
    # How this row was detected: browser_history | manual
    source = Column(String(40), nullable=True)
    notes = Column(Text, nullable=True)
    last_checked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SubscriptionAction(Base):
    """Per-subscription audit trail: every login, navigation, and cancel step.

    Cancellation is two-phase: a ``cancel_requested`` row (status
    ``pending_confirm``) is written when the user asks to cancel, and a separate
    ``cancel_confirmed`` row records the irreversible step only after an explicit
    confirmation.
    """
    __tablename__ = "subscription_actions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    subscription_id = Column(Integer, nullable=False, index=True)
    action = Column(String(40), nullable=False)
    # ok | error | blocked | needs_user | pending_confirm
    status = Column(String(20), nullable=False, default="ok")
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class AppSetting(Base):
    """Persisted non-secret application settings (e.g. capability toggles).

    Kept separate from the credential vault so feature flags are not stored or
    surfaced as secrets. Values are synced into the process environment at boot
    so they are read through the same env-based config path as everything else.
    """
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(120), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class GovernanceProposal(Base):
    """An OpenGov-style governance proposal raised by the auditor, sentinel, or operator.

    Origins: auditor | sentinel | operator | system
    Tracks:  tuning (≥50% quorum) | policy (≥66%) | safety (≥75%)
    Status:  open | enacted | rejected | withdrawn
    """
    __tablename__ = "governance_proposals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    proposal_id = Column(String(30), unique=True, nullable=False, index=True)
    origin = Column(String(50), nullable=False, default="system")
    track = Column(String(20), nullable=False, default="tuning")
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    proposed_action = Column(Text, nullable=True)
    evidence_json = Column(Text, nullable=True)
    quorum_threshold = Column(Float, default=0.50)
    status = Column(String(20), nullable=False, default="open", index=True)
    source_claim_id = Column(String(50), nullable=True)
    enacted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class GovernanceVote(Base):
    """A vote cast on a governance proposal.

    vote: yes | no | abstain | delegated
    weight: typically 1.0 for operator; multi-provider quorum may use fractional weights
    """
    __tablename__ = "governance_votes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    proposal_id = Column(Integer, nullable=False, index=True)
    voter = Column(String(100), nullable=False)
    vote = Column(String(20), nullable=False)
    weight = Column(Float, default=1.0)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class GovernanceEnactment(Base):
    """Audit record written when a proposal is enacted (quorum or operator-direct).

    change_json captures the enacted action, method, and tally snapshot so the
    full audit trail (proposal → vote → enactment) is permanently recoverable.
    """
    __tablename__ = "governance_enactments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    proposal_id = Column(Integer, nullable=False, index=True)
    enacted_by = Column(String(100), nullable=False)
    change_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class SwarmRunIndex(Base):
    """Compact, searchable index record written at the end of every swarm run.

    Enables full-text search across goals, epistemic categories, provenance
    lineage, and dissent flags from the /runs/search dashboard view.
    Links back to the TaskRun row (task_run_id) for the full result detail.
    """
    __tablename__ = "swarm_run_index"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(36), unique=True, nullable=False, index=True)
    goal = Column(Text, nullable=False)
    domain = Column(String(100), nullable=True)
    synthesis = Column(Text, nullable=True)
    epistemic_summary_json = Column(Text, nullable=True)
    provenance_lineage_json = Column(Text, nullable=True)
    claim_text = Column(Text, nullable=True)
    dissent_json = Column(Text, nullable=True)
    dissent_count = Column(Integer, default=0)
    aborted = Column(Boolean, default=False)
    avg_cs = Column(Float, nullable=True)
    avg_hr = Column(Float, nullable=True)
    meta_claims_count = Column(Integer, default=0)
    task_run_id = Column(Integer, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
