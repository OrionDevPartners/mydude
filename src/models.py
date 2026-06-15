from datetime import datetime
from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, Boolean, DateTime, Float, JSON,
    LargeBinary,
    UniqueConstraint,
    ForeignKey
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
    # Who performed the action. Nullable so historical rows (written before
    # per-user accounts existed) and dev-bypass actions remain representable.
    actor_user_id = Column(Integer, nullable=True, index=True)
    actor_username = Column(String(80), nullable=True)
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
    # Where the capability executed (local / in_azure / cloud). Captured so the
    # audit trail records jurisdiction, not just the action — governance pillar #4.
    exec_locus = Column(String(20), nullable=True)
    # Who triggered the invocation. Nullable so system/agent-initiated calls and
    # historical rows (written before identity capture) remain representable.
    actor_user_id = Column(Integer, nullable=True, index=True)
    actor_username = Column(String(80), nullable=True)
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


class DecisionTrace(Base):
    """One governed-turn audit record emitted by Cogitation.think().

    Every agent path (coach, fleet, api, subsystem) that flows through the
    Cogitation entrypoint writes exactly one DecisionTrace per turn so every
    reasoning decision is auditable and queryable.
    """
    __tablename__ = "decision_traces"

    id = Column(Integer, primary_key=True, autoincrement=True)
    turn_id = Column(String(36), nullable=False, index=True)
    source = Column(String(60), nullable=False, index=True)
    goal_preview = Column(String(500), nullable=True)
    stages_json = Column(Text, nullable=True)
    jurisdiction_json = Column(Text, nullable=True)
    avg_cs = Column(Float, nullable=True)
    avg_hr = Column(Float, nullable=True)
    hr_tier = Column(String(20), nullable=True)
    provenance_summary = Column(Text, nullable=True)
    tool_calls_json = Column(Text, nullable=True)
    outcome = Column(String(30), nullable=False, default="completed")
    aborted = Column(Boolean, default=False)
    task_run_id = Column(Integer, nullable=True, index=True)
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


# ---------------------------------------------------------------------------
# Self-evolving prompt engine (DSPy + MIPROv2/GEPA), governance-gated.
#
# A PromptProgram is a named, optimizable behavior whose live instructions are
# served at runtime through a DSPy Signature/Module (see src/promptopt/). Every
# governed run writes a PromptTrace the optimizers consume. Optimizers produce
# candidate PromptVersions; a candidate only goes LIVE through the existing
# GovernanceProposal/Vote/Enactment gate (no auto-promotion). Rollback is a
# direct, audited operator action restricted to previously-live versions.
# ---------------------------------------------------------------------------


class PromptProgram(Base):
    """A governed, optimizable behavior whose instructions can evolve.

    ``current_version_id`` points at the LIVE PromptVersion served at runtime.
    The program set is seeded idempotently at startup from the code's current
    hardcoded prompts (version 1, live).
    """
    __tablename__ = "prompt_programs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), unique=True, nullable=False, index=True)
    signature_name = Column(String(120), nullable=False)
    description = Column(Text, nullable=True)
    current_version_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PromptVersion(Base):
    """An immutable instructions+demos snapshot for a PromptProgram.

    status:     candidate (proposed by an optimizer) | live (currently served)
                | archived (was live, superseded) | rejected.
    ever_live:  True once the version has been served — the ONLY valid rollback
                targets, so a rollback can never point at an unapproved candidate.
    governance_proposal_id: the enacted proposal that promoted this version
                (provenance + permanent audit linkage).
    """
    __tablename__ = "prompt_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    program_id = Column(Integer, nullable=False, index=True)
    version_no = Column(Integer, nullable=False)
    instructions = Column(Text, nullable=False)
    demos_json = Column(Text, nullable=True)
    provenance_json = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="candidate", index=True)
    ever_live = Column(Boolean, default=False)
    score = Column(Float, nullable=True)
    governance_proposal_id = Column(Integer, nullable=True)
    optimization_run_id = Column(Integer, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    promoted_at = Column(DateTime, nullable=True)


class PromptTrace(Base):
    """A structured execution trace of a governed program run.

    Consumed by the optimizers as trainset rows. Captures inputs, the produced
    output, the composite score, and structured feedback (missing format
    sections, compliance violations). status='failed' rows record degraded runs
    loudly without being fed back into optimization as good examples.
    """
    __tablename__ = "prompt_traces"

    id = Column(Integer, primary_key=True, autoincrement=True)
    program_id = Column(Integer, nullable=False, index=True)
    version_id = Column(Integer, nullable=True, index=True)
    inputs_json = Column(Text, nullable=True)
    output = Column(Text, nullable=True)
    score = Column(Float, nullable=True)
    compliance_score = Column(Integer, nullable=True)
    hallucination_risk = Column(Float, nullable=True)
    feedback_json = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="ok", index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PromptOptimizationRun(Base):
    """An optimizer execution (MIPROv2 baseline, then GEPA reflective).

    Polled by the dashboard. status: running | completed | failed. On a fail-loud
    error (e.g. no provider available, optimizer exception) status='failed' and
    ``error`` carries the message — never a silent fallback. ``candidates_json``
    holds produced candidate version ids + measured scores.
    """
    __tablename__ = "prompt_optimization_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    program_id = Column(Integer, nullable=False, index=True)
    optimizer = Column(String(40), nullable=False, default="mipro+gepa")
    status = Column(String(20), nullable=False, default="running", index=True)
    trainset_size = Column(Integer, default=0)
    base_score = Column(Float, nullable=True)
    best_score = Column(Float, nullable=True)
    candidates_json = Column(Text, nullable=True)
    log = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    started_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


# ---------------------------------------------------------------------------
# Finance / accountant sub-stack (QuickBooks + Plaid)
#
# Postgres is the system of record for raw financial data. Only relation-level
# claims (vendor -> project edges, aggregates) are written to the shared memory
# substrate — never full per-transaction ledger lines — because the memory cloud
# adapter may egress content to an external service. Read-only by default; every
# write to QuickBooks goes through FinanceWriteRequest (two-phase approval) and is
# audited in FinanceAuditLog.
# ---------------------------------------------------------------------------


class FinanceProject(Base):
    """A project or LLC that transactions/vendors are attributed to (e.g. "NSB-1194")."""
    __tablename__ = "finance_projects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(60), unique=True, nullable=False, index=True)
    name = Column(String(200), nullable=False)
    llc = Column(String(200), nullable=True)
    description = Column(Text, nullable=True)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# Bot Fleet & Provisioning Engine
# ---------------------------------------------------------------------------

class Team(Base):
    """A named group of bots that collaborate through the swarm orchestrator.

    spawn_cap — operator-set ceiling on auto-spawned bots per team (never unbounded).
    status    — defined | running | stopped | error
    """
    __tablename__ = "bot_teams"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=True)
    spawn_cap = Column(Integer, default=5)
    status = Column(String(30), default="defined", index=True)
    memory_namespace = Column(String(120), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FinanceBudget(Base):
    """A budget line for a project. ``category`` None means the project-wide total."""
    __tablename__ = "finance_budgets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, nullable=False, index=True)
    category = Column(String(120), nullable=True)
    period = Column(String(20), default="total")  # total | monthly
    amount = Column(Float, nullable=False, default=0.0)
    currency = Column(String(10), default="USD")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Bot(Base):
    """A persistent autonomous agent with its own identity, goal, and capability set.

    identity_schema — JSON: name, role, personality traits, communication style
    prompt_cards    — JSON list of prompt fragments injected into the swarm persona
    protocols       — JSON list of free-text operator rules ("always ask Bob before ...")
    allowed_caps    — JSON list of capability names this bot may request via broker
    lifecycle       — defined | provisioning | running | stopped | failed
    team_id         — FK to bot_teams; null for solo bots
    spawned_by_id   — FK to bot.id; set when a running bot requested this spawn
    """
    __tablename__ = "bots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=True)
    team_id = Column(Integer, ForeignKey("bot_teams.id"), nullable=True, index=True)
    spawned_by_id = Column(Integer, ForeignKey("bots.id"), nullable=True, index=True)
    identity_schema = Column(JSON, nullable=True)
    prompt_cards = Column(JSON, nullable=True)
    goal = Column(Text, nullable=True)
    protocols = Column(JSON, nullable=True)
    allowed_caps = Column(JSON, nullable=True)
    # Operator-configured sales-mode script (opener, qualification questions,
    # closing prompt, question cap, AI-disclosure text, qualification threshold).
    # Null when the bot is not configured for sales conversations.
    sales_config = Column(JSON, nullable=True)
    # Voice + telephony (Task #66). voice_id selects the ElevenLabs voice used for
    # TTS on calls; phone_number is the bot's provider-owned E.164 caller-ID and the
    # number inbound calls are routed from. Both null until the operator assigns them.
    voice_id = Column(String(120), nullable=True)
    phone_number = Column(String(40), nullable=True, index=True)
    lifecycle = Column(String(30), default="defined", index=True)
    last_run_at = Column(DateTime, nullable=True)
    last_task_run_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SalesConversation(Base):
    """A governed sales conversation conducted by a bot in sales mode.

    A deterministic phase engine (opener → qualify → close → booked/ended)
    drives the flow; bot phrasing is governed by the LLM swarm (compliance /
    hallucination scored, with the operator's pre-approved script as the
    fail-safe). The number of qualification questions is hard-capped and the
    bot ALWAYS discloses it is an AI when asked.

    phase      — opener | qualify | close | booked | ended
    status     — active | booked | disqualified | ended
    transcript — JSON list of {role, text, phase, governance, degraded, ts}
    """
    __tablename__ = "sales_conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    bot_id = Column(Integer, ForeignKey("bots.id"), nullable=False, index=True)
    prospect_name = Column(String(200), nullable=True)
    prospect_contact = Column(String(255), nullable=True)
    phase = Column(String(20), default="opener", index=True)
    status = Column(String(20), default="active", index=True)
    qualified = Column(Boolean, default=False)
    questions_asked = Column(Integer, default=0)
    disclosed_ai = Column(Boolean, default=False)
    transcript = Column(JSON, nullable=True)
    booking_url = Column(String(1000), nullable=True)
    booking_ref = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CallSession(Base):
    """A governed phone call conducted by a bot via the telephony layer (Task #66).

    Every call is broker-gated and capability-audited like all other bot actions.
    The turn-based conversation (provider speech-gather for STT, ElevenLabs TTS
    played back) is governed through Cogitation, so each spoken reply is backed by
    a DecisionTrace (``last_decision_trace_id``).

    direction — inbound | outbound
    status    — queued | ringing | in_progress | completed | failed | busy
                | no_answer | canceled
    transcript — JSON list of {role, text, governance, degraded, trace_id, ts}
    """
    __tablename__ = "call_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    bot_id = Column(Integer, ForeignKey("bots.id"), nullable=False, index=True)
    provider = Column(String(40), nullable=False, default="twilio")
    direction = Column(String(20), nullable=False, default="outbound", index=True)
    status = Column(String(20), nullable=False, default="queued", index=True)
    from_number = Column(String(40), nullable=True)
    to_number = Column(String(40), nullable=True)
    provider_call_sid = Column(String(120), nullable=True, unique=True, index=True)
    transcript = Column(JSON, nullable=True)
    last_decision_trace_id = Column(Integer, nullable=True)
    turns = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CallAudio(Base):
    """Short-lived TTS audio served to the telephony provider by public URL.

    Telephony providers (e.g. Twilio ``<Play>``) fetch audio over HTTP, so each
    synthesized MP3 is parked here behind a high-entropy token with a TTL and
    served ``no-store``. Rows are disposable and pruned after expiry; they hold no
    secrets and the token is unguessable.
    """
    __tablename__ = "call_audio"

    id = Column(Integer, primary_key=True, autoincrement=True)
    token = Column(String(64), nullable=False, unique=True, index=True)
    call_session_id = Column(Integer, ForeignKey("call_sessions.id"), nullable=True, index=True)
    content_type = Column(String(60), nullable=False, default="audio/mpeg")
    audio_bytes = Column(LargeBinary, nullable=False)
    expires_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class FinanceVendor(Base):
    """A vendor/merchant seen in transactions or QuickBooks entities."""
    __tablename__ = "finance_vendors"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_vendor_source_extid"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(20), nullable=False, default="manual")  # plaid | quickbooks | manual
    external_id = Column(String(120), nullable=True)
    name = Column(String(255), nullable=False)
    normalized_name = Column(String(255), nullable=True, index=True)
    default_project_id = Column(Integer, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProvisionedResource(Base):
    """A cloud resource (VM, Git repo, ML service) created for a bot or team.

    resource_type — vm | git_repo | ml_service | other
    provider      — aws | gcp | azure | github | mlflow | sagemaker | stub
    status        — planned | pending_approval | provisioning | active | failed | destroyed
    resource_id   — provider-assigned ID/ARN/URL once provisioned
    plan_output   — captured plan text (terraform plan / SDK dry-run) for operator review
    apply_output  — captured apply/create output after operator approval
    """
    __tablename__ = "provisioned_resources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    bot_id = Column(Integer, ForeignKey("bots.id"), nullable=True, index=True)
    team_id = Column(Integer, ForeignKey("bot_teams.id"), nullable=True, index=True)
    resource_type = Column(String(60), nullable=False)
    provider = Column(String(60), nullable=False, default="stub")
    name = Column(String(200), nullable=True)
    resource_id = Column(String(500), nullable=True)
    status = Column(String(40), default="planned", index=True)
    plan_output = Column(Text, nullable=True)
    apply_output = Column(Text, nullable=True)
    config_json = Column(JSON, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    provisioned_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FinanceTransaction(Base):
    """A bank/card transaction (Plaid) or QuickBooks line, the raw system of record."""
    __tablename__ = "finance_transactions"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_txn_source_extid"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(20), nullable=False, default="plaid")  # plaid | quickbooks
    external_id = Column(String(120), nullable=False)
    account = Column(String(120), nullable=True)
    txn_date = Column(DateTime, nullable=True, index=True)
    amount = Column(Float, nullable=False, default=0.0)
    currency = Column(String(10), default="USD")
    name = Column(String(255), nullable=True)        # merchant / payee
    memo = Column(Text, nullable=True)
    category_raw = Column(String(255), nullable=True)
    pending = Column(Boolean, default=False)
    pending_external_id = Column(String(120), nullable=True, index=True)
    vendor_id = Column(Integer, nullable=True, index=True)
    project_id = Column(Integer, nullable=True, index=True)
    # attributed | unattributed | manual
    attribution_status = Column(String(20), default="unattributed", index=True)
    attribution_confidence = Column(Float, default=0.0)
    attribution_method = Column(String(40), nullable=True)
    raw_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PlaidItem(Base):
    """A linked Plaid Item (one bank login) with its encrypted access token.

    Created when a user completes Plaid Link and the resulting ``public_token`` is
    exchanged SERVER-SIDE for a long-lived ``access_token``. The token is
    encrypted at rest (Fernet, via ``src/web/crypto``) and is NEVER returned to
    the client. Each Item carries its own ``/transactions/sync`` cursor — Plaid
    cursors are access-token scoped, so a single global cursor cannot support
    multiple Items. ``source`` distinguishes Link-created items ("link") from the
    legacy single env/connector token ("env")."""
    __tablename__ = "plaid_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(String(120), unique=True, nullable=False, index=True)
    encrypted_access_token = Column(Text, nullable=False)
    institution_name = Column(String(200), nullable=True)
    institution_id = Column(String(120), nullable=True)
    cursor = Column(Text, nullable=True)                        # per-item sync cursor
    status = Column(String(20), default="active", index=True)   # active | error | removed
    last_error = Column(Text, nullable=True)
    source = Column(String(20), default="link")                 # link | env
    last_synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class VendorProjectRule(Base):
    """An explicit rule mapping a vendor name match to a project (deterministic attribution)."""
    __tablename__ = "vendor_project_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_text = Column(String(255), nullable=False, index=True)  # normalized substring
    project_id = Column(Integer, nullable=False, index=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class FinanceSyncRun(Base):
    """Audit row for each ingest run (on demand or scheduled). Read-only ingest."""
    __tablename__ = "finance_sync_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(30), nullable=False)  # plaid | quickbooks | all
    trigger = Column(String(20), default="manual")  # manual | scheduled
    status = Column(String(20), default="running")  # running | ok | error | skipped
    transactions_ingested = Column(Integer, default=0)
    entities_ingested = Column(Integer, default=0)
    removed_count = Column(Integer, default=0)
    attributed_count = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    detail = Column(Text, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class FinanceWriteRequest(Base):
    """A two-phase, approval-gated write to QuickBooks. Created in ``pending_confirm``;
    only ``confirm`` (after explicit operator approval) executes it. QuickBooks
    remains the system of record."""
    __tablename__ = "finance_write_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String(40), nullable=False)  # categorize | create_bill | create_invoice
    target_external_id = Column(String(120), nullable=True)
    payload_json = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    # pending_confirm | executed | failed | rejected
    status = Column(String(20), default="pending_confirm", index=True)
    result_detail = Column(Text, nullable=True)
    requested_at = Column(DateTime, default=datetime.utcnow)
    confirmed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class FinanceAuditLog(Base):
    """Audit trail for finance actions: sync, attribution, and gated writes."""
    __tablename__ = "finance_audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    action = Column(String(60), nullable=False)
    status = Column(String(20), nullable=False, default="ok")
    detail = Column(Text, nullable=True)
    source = Column(String(40), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Coach / Secretary / Mood sub-stack (Personal Assistant + Life Coach)
# ---------------------------------------------------------------------------

class MoodSignal(Base):
    """A time-stamped emotion/behavior node — the longitudinal 'digital twin'.

    Postgres is the system of record. Each row optionally links to a LOCAL-ONLY
    memory node (``memory_id``) so emotional content lives in the local knowledge
    graph but never egresses to the cloud adapter (Private-Mode)."""
    __tablename__ = "mood_signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # emotion (Hume) | sentiment (LLM) | behavior (calendar/finance)
    signal_type = Column(String(20), nullable=False, default="emotion", index=True)
    # hume | llm_sentiment | calendar | finance | manual
    source = Column(String(40), nullable=False, default="manual")
    observed_at = Column(DateTime, nullable=True, index=True)
    valence = Column(Float, nullable=True)   # -1..1 overall pleasantness
    arousal = Column(Float, nullable=True)   # 0..1 activation / intensity
    score = Column(Float, nullable=True)     # generic magnitude (e.g. stress level)
    label = Column(String(80), nullable=True)   # dominant emotion / behavior label
    summary = Column(Text, nullable=True)       # human-readable note
    metrics_json = Column(Text, nullable=True)  # full provider payload (top emotions, etc.)
    project_id = Column(Integer, nullable=True, index=True)  # link to a finance/work project
    event_ref = Column(String(120), nullable=True)          # calendar event / context ref
    memory_id = Column(String(80), nullable=True, index=True)  # local-only memory node id (purge)
    private = Column(Boolean, default=True)   # emotional content never egresses
    created_at = Column(DateTime, default=datetime.utcnow)


class CoachInsight(Base):
    """A surfaced longitudinal pattern (e.g. burnout risk) with grounded citations
    and a concrete micro-action. Outcome is logged back for closed-loop coaching."""
    __tablename__ = "coach_insights"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String(40), nullable=False, default="pattern")  # pattern | risk | reflection
    title = Column(String(200), nullable=False)
    detail = Column(Text, nullable=True)
    severity = Column(String(20), default="info")  # info | watch | elevated | high
    micro_action = Column(Text, nullable=True)
    citations_json = Column(Text, nullable=True)   # memory_ids / signal ids grounding the insight
    confidence = Column(Float, default=0.0)
    # open | acknowledged | actioned | dismissed
    status = Column(String(20), default="open", index=True)
    outcome = Column(Text, nullable=True)
    source = Column(String(40), default="reflection")  # reflection | manual
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SecretaryRequest(Base):
    """A two-phase, approval-gated outbound action (email/text/booking). Created in
    ``pending_confirm``; only ``confirm`` after explicit operator approval dispatches
    it via the provider-agnostic delivery layer. Fails loud if no provider configured."""
    __tablename__ = "secretary_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String(40), nullable=False)     # draft_email | draft_text | propose_booking
    channel = Column(String(20), nullable=False)  # email | sms | calendar
    recipient = Column(String(255), nullable=True)
    subject = Column(String(255), nullable=True)
    body = Column(Text, nullable=True)
    payload_json = Column(Text, nullable=True)  # channel-specific (start/end/attendees for booking)
    summary = Column(Text, nullable=True)
    # pending_confirm | sent | failed | rejected | needs_provider
    status = Column(String(20), default="pending_confirm", index=True)
    provider = Column(String(40), nullable=True)
    result_detail = Column(Text, nullable=True)
    requested_at = Column(DateTime, default=datetime.utcnow)
    confirmed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class CoachAuditLog(Base):
    """Audit trail for coach/secretary actions: ingest, ask, reflect, gated outbound."""
    __tablename__ = "coach_audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    action = Column(String(60), nullable=False)
    status = Column(String(20), nullable=False, default="ok")
    detail = Column(Text, nullable=True)
    source = Column(String(40), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Avatar / persona / voice sub-stack (Humanistic avatar layer — Azure-tier)
#
# Gives a bot a presentation identity (persona + voice + avatar provider). Voice
# (ElevenLabs TTS) is Replit-native. Realistic real-time avatar VIDEO runs on the
# EXTERNAL GPU stack (HeyGen Streaming / Azure-hosted NVIDIA ACE) — the container
# only NEGOTIATES the session over HTTPS and hands the browser WebRTC connection
# info; it never hosts GPU rendering or relays media. Every live session enforces
# AI-use disclosure + recording consent before it can go active, with a voice-only
# fallback when the avatar backend is unavailable.
# ---------------------------------------------------------------------------


class AvatarProfile(Base):
    """A bot's presentation identity: persona text + voice + avatar provider.

    Deliberately fleet-generic (``name``/``persona``/``active``/``bot_id``) so the
    Bot Fleet sub-stack can link a bot to its profile via ``bot_id`` and extend this
    table additively (auto-migration only adds columns). Avatar/voice specifics live
    in the ``*_provider`` / ``*_id`` / ``avatar_config_json`` columns. Provider names
    are stored here but credentials NEVER are — those are sourced at runtime via the
    connector proxy / vault.
    """
    __tablename__ = "avatar_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), unique=True, nullable=False, index=True)
    persona = Column(Text, nullable=True)
    # Optional link to a future fleet bot (Bot Fleet sub-stack). Nullable so the
    # avatar layer stands alone until the fleet exists.
    bot_id = Column(Integer, nullable=True, index=True)
    voice_provider = Column(String(40), nullable=True, default="elevenlabs")
    voice_id = Column(String(120), nullable=True)
    # heygen | azure | nvidia-ace | custom — the external GPU avatar backend.
    avatar_provider = Column(String(40), nullable=True)
    avatar_config_json = Column(Text, nullable=True)  # provider-specific (avatar_id, quality, ...)
    # AI-use disclosure + recording consent are mandatory by default for call flows.
    disclosure_required = Column(Boolean, default=True)
    consent_required = Column(Boolean, default=True)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AvatarSession(Base):
    """A live (or attempted) interaction session for an AvatarProfile.

    Two-phase like the secretary gate: a session starts in ``pending_consent`` and
    cannot reach ``active`` until disclosure has been shown and recording consent is
    granted. Bridge negotiation happens only AFTER consent commits; if the avatar
    backend is unavailable the session degrades honestly to ``voice_only`` (or
    ``needs_provider`` when nothing is configured) — connection info is never faked.

    ``connection_json`` holds the ephemeral provider WebRTC/LiveKit session info the
    browser needs. It can contain short-lived session tokens, so it is cleared on
    end and is NEVER written to the audit log.
    """
    __tablename__ = "avatar_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    avatar_profile_id = Column(Integer, nullable=False, index=True)
    # avatar_video | voice_only
    mode = Column(String(20), nullable=True)
    # pending_consent | active | voice_only | needs_provider | denied | blocked | ended
    status = Column(String(20), nullable=False, default="pending_consent", index=True)
    provider = Column(String(40), nullable=True)
    disclosure_shown = Column(Boolean, default=False)
    # pending | granted | denied
    consent_status = Column(String(20), nullable=False, default="pending")
    consent_detail = Column(Text, nullable=True)
    connection_json = Column(Text, nullable=True)  # ephemeral; never audited; cleared on end
    result_detail = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class AvatarAuditLog(Base):
    """Audit trail for avatar actions: profile CRUD, session lifecycle, consent,
    disclosure, bridge negotiation, and voice-only degradation. Never stores keys
    or connection tokens."""
    __tablename__ = "avatar_audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    action = Column(String(60), nullable=False)
    status = Column(String(20), nullable=False, default="ok")
    detail = Column(Text, nullable=True)
    source = Column(String(40), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ProvisioningJob(Base):
    """Audit record for a provisioning run: plan → approve → apply.

    Captures every phase transition so the full governance trail is recoverable.
    status — planning | awaiting_approval | applying | done | failed
    """
    __tablename__ = "provisioning_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    bot_id = Column(Integer, ForeignKey("bots.id"), nullable=True, index=True)
    team_id = Column(Integer, ForeignKey("bot_teams.id"), nullable=True, index=True)
    resource_id = Column(Integer, ForeignKey("provisioned_resources.id"), nullable=True, index=True)
    status = Column(String(40), default="planning", index=True)
    requested_config = Column(JSON, nullable=True)
    plan_summary = Column(Text, nullable=True)
    apply_summary = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    planned_at = Column(DateTime, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    applied_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ---------------------------------------------------------------------------
# Edge-truth / thesis self-evolution loop (EXPERIMENTAL sandbox)
# ---------------------------------------------------------------------------

class CognitionComponent(Base):
    """A cognition component whose 'edge truth' (champion conclusion) evolves.

    Each component has a current truth snapshot and participates in a perpetual
    self-improvement loop where improvement theses (challengers) are tested in an
    EXPERIMENTAL sandbox and promoted only through the governance gate.

    component_type:
      prompt_program   — a PromptProgram (links truth_version_id)
      swarm_config     — a swarm parameter bundle (AppSetting keys)
      role_composition — cognitive role weights per wave

    loop_state: idle | running | paused | error
    """
    __tablename__ = "cognition_components"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), unique=True, nullable=False, index=True)
    component_type = Column(String(40), nullable=False, default="prompt_program")
    description = Column(Text, nullable=True)
    truth_json = Column(Text, nullable=True)
    truth_version_id = Column(Integer, nullable=True)
    loop_state = Column(String(20), default="idle", index=True)
    loop_enabled = Column(Boolean, default=False)
    cycle_count = Column(Integer, default=0)
    last_cycle_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CognitionThesis(Base):
    """A proposed improvement to exactly one branch cell of a cognition component.

    Targets a single branch cell (e.g. 'instructions', 'consensus_threshold',
    'role_weights.skeptic') so promotion swaps only that cell — the whole brain
    inherits the upgrade by default without touching the rest of the system.

    Status lifecycle:
      proposed → testing → awaiting_consensus → promoted | rejected | stalled
    """
    __tablename__ = "cognition_theses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    component_id = Column(Integer, ForeignKey("cognition_components.id"), nullable=False, index=True)
    branch_cell = Column(String(120), nullable=False)
    thesis_json = Column(Text, nullable=False)
    rationale = Column(Text, nullable=True)
    status = Column(String(30), default="proposed", index=True)
    test_score = Column(Float, nullable=True)
    base_score = Column(Float, nullable=True)
    governance_proposal_id = Column(String(80), nullable=True)
    governance_proposal_db_id = Column(Integer, nullable=True)
    requires_human_gate = Column(Boolean, default=False)
    trial_iteration_count = Column(Integer, default=0)
    stalled_at = Column(DateTime, nullable=True)
    cycle_index = Column(Integer, default=0)
    selection_votes_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ThesisTrialIteration(Base):
    """One build/test iteration within an EXPERIMENTAL thesis trial.

    All iterations run inside the EXPERIMENTAL sandbox — they never read from or
    write to the live truth path. The sandbox_label column is always 'EXPERIMENTAL'
    and every row here is the complete, auditable record of what ran and what scored.
    """
    __tablename__ = "thesis_trial_iterations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    thesis_id = Column(Integer, ForeignKey("cognition_theses.id"), nullable=False, index=True)
    iteration_no = Column(Integer, nullable=False)
    sandbox_label = Column(String(20), default="EXPERIMENTAL", nullable=False)
    test_results_json = Column(Text, nullable=True)
    compliance_score = Column(Float, nullable=True)
    hallucination_risk = Column(Float, nullable=True)
    composite_score = Column(Float, nullable=True)
    all_tests_passed = Column(Boolean, default=False)
    outcome = Column(String(20), nullable=False, default="pending")
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class EvolutionCycleLog(Base):
    """Audit log for each completed/failed/stalled evolution cycle.

    Created at the end of every cycle (whether the thesis was promoted, rejected,
    or stalled). Also records how the NEXT thesis was selected so the selection
    logic is fully auditable.
    """
    __tablename__ = "evolution_cycle_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    component_id = Column(Integer, ForeignKey("cognition_components.id"), nullable=False, index=True)
    cycle_index = Column(Integer, nullable=False)
    outcome = Column(String(20), nullable=False)
    thesis_id = Column(Integer, nullable=True)
    next_thesis_selection_json = Column(Text, nullable=True)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)



class MemoryEntryRecord(Base):
    """Durable persistence of a substrate MemoryEntry.

    Mirrors the ``MemoryEntry`` dataclass (src/memory/adapter.py) so the local
    (Cognee) and cloud (Mem0) adapter caches survive process restarts and
    redeploys. The Cognee KG is JSON-persisted to disk and Mem0 has a local-file
    fallback, but those are process/host-local; this table is the durable source
    of truth for "which entries exist" across the whole stack.

    ``adapter`` discriminates which side wrote the row ("local" | "cloud"); the
    same logical entry can exist on both sides keyed by the same ``memory_id``,
    so uniqueness is on the (memory_id, adapter) pair. Timestamps mirror the
    dataclass's float epoch fields exactly so values round-trip without drift.
    """
    __tablename__ = "memory_entries"
    __table_args__ = (
        UniqueConstraint("memory_id", "adapter", name="uq_memory_entry_id_adapter"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    memory_id = Column(String(64), nullable=False, index=True)
    adapter = Column(String(20), nullable=False, index=True)
    content = Column(Text, nullable=False)
    category = Column(String(60), nullable=True)
    confidence = Column(Float, default=1.0)
    source = Column(Text, nullable=True)
    entry_created_at = Column(Float, nullable=True)
    entry_updated_at = Column(Float, nullable=True)
    access_count = Column(Integer, default=0)
    decay = Column(Float, default=1.0)
    verified = Column(Boolean, default=False)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BurstWorker(Base):
    """An ephemeral burst compute worker provisioned by the BurstManager.

    Lifecycle states:
      provisioning → active → terminated | failed
    Created when saturation exceeds the burst threshold and jurisdiction
    permits cloud egress.  Torn down when saturation drains.

    backend   — which backend adapter provisioned this worker (modal, ray, …)
    worker_id — UUID4 assigned by BurstManager, stable across retries
    status    — provisioning | active | terminated | failed
    """
    __tablename__ = "burst_workers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    worker_id = Column(String(36), nullable=False, unique=True, index=True)
    backend = Column(String(60), nullable=False)
    status = Column(String(30), nullable=False, default="provisioning", index=True)
    config_json = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    provisioned_at = Column(DateTime, default=datetime.utcnow)
    torn_down_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class BurstEvent(Base):
    """Audit trail of every burst worker lifecycle transition.

    event_type values:
      provisioned | provision_failed | dispatched | dispatch_failed |
      teardown_started | torn_down | burst_blocked | drain_started
    """
    __tablename__ = "burst_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    worker_id = Column(String(36), nullable=False, index=True)
    db_worker_id = Column(Integer, nullable=True, index=True)
    event_type = Column(String(40), nullable=False, index=True)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class MemoryAuditLog(Base):
    """Durable audit trail of substrate MemoryEvents.

    Mirrors the ``MemoryEvent`` dataclass (src/memory/adapter.py). The substrate
    keeps a bounded in-process deque for fast dashboard reads; this table makes
    the audit trail durable so recall/persist/sync/consolidate history survives
    restarts and is permanently queryable.
    """
    __tablename__ = "memory_audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(20), nullable=False, index=True)
    detail = Column(Text, nullable=True)
    memory_ids_json = Column(Text, nullable=True)
    event_ts = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class User(Base):
    """An individual operator account.

    Replaces the single shared admin password with per-person credentials so
    every privileged action is attributable and one account can be revoked
    without affecting the others. The password is stored only as a bcrypt hash;
    the plaintext is never persisted. ``is_admin`` gates user-management.
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(80), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=True, index=True)
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)
