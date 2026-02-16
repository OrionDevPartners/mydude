# Telegram Bot - Replit Manager + Porter Swarm

## Overview
A Telegram bot for managing tasks, notes, shell commands, git operations, and multi-agent swarm orchestration on a Replit project. Built with Python 3.11, python-telegram-bot v22, SQLAlchemy, and PostgreSQL. Features a self-healing protocol with circuit breakers, health monitoring, automatic secret loading from 1Password, 16 advanced features, and a full cognitive architecture for LLM epistemic governance including hallucination abolition, compliance scoring, and structured debate protocols.

## Project Structure
```
main.py              - Entry point (loads 1Password secrets before bot start)
src/
  op_secrets.py      - 1Password CLI integration for secret loading at startup
  bot.py             - Bot application setup, polling/webhook, health monitor, cron/digest init
  database.py        - SQLAlchemy engine, session, and Base
  models.py          - Database models (Task, Note, CommandLog, UserSettings, AuditLog,
                       ConversationMemory, Goal, CronJob, ProviderMetric, IntegrationConfig,
                       PipelineTrigger, DigestConfig)
  handlers/
    help.py          - /start, /help, /authorize, /whoami
    shell.py         - /shell command execution
    tasks.py         - /addtask, /tasks, /donetask, /deltask
    notes.py         - /addnote, /notes, /viewnote, /delnote
    git.py           - /gitstatus, /gitlog, /gitdiff, /gitcommit, /gitpull, /gitpush
    swarm.py         - /goal, /waves, /policy (Porter swarm commands)
    selfheal.py      - /selfheal, /healcheck (self-healing protocol status)
    extract.py       - /extract (AI content analysis + Asana task creation)
    audit.py         - /audit (command history, search)
    memory.py        - /memory (conversation memory overview)
    goals.py         - /goals, /goalstatus, /goalcomplete
    cron_handler.py  - /cron (scheduled job management)
    digest.py        - /digest (recurring digest config)
    voice.py         - Voice note transcription handler
    ingest.py        - /ingest, document ingestion handler
    rag.py           - /askcode, /codestructure (RAG over codebase)
    triage.py        - /triage (auto-classification), /metrics (provider stats)
    integrations.py  - /connect, /integrations, /slack, /discord, /ghissue, /ghissues,
                       /linearissue, /linearissues, /pipeline
    cognition.py     - /constitution, /cognition (cognitive architecture status)
  swarm/
    prompts.py       - System prompts with constitution rules injected
    constitution.py  - Agent Constitution: epistemic categories, claim ledger, intent binding,
                       dual-mode reasoning, language discipline, stop conditions
    compliance.py    - Runtime Compliance Scoring (CS 0-100): 8-term penalty formula,
                       tier gates, auto-correction protocol
    hallucination.py - Hallucination Risk Model (HR 0-1): 8 weighted features, tiered
                       control actions, risk monitoring with abort capability
    contract.py      - Swarm Orchestration Contract: 6 cognitive roles, 7-round debate
                       cycle, weighted consensus, dissent preservation
    policy.py        - PolicyEngine (production gates, secret protection)
    broker.py        - CapabilityBroker (agents request, broker enforces + executes)
    integrations.py  - Safe integration layer (git, terraform, asana, 1password stubs)
    orchestrator.py  - WaveOrchestrator + cognitive architecture integration
    llm_multi.py     - MultiProviderLLM with compliance-weighted consensus
    model_resolver.py - Auto-resolve latest model per provider family
    utils.py         - safe_json_dumps, clamp_list
  selfheal/
    __init__.py      - Exports CircuitBreaker and HealthMonitor
    circuit_breaker.py - Per-provider circuit breaker (closed/open/half_open states)
    health_monitor.py  - Background health checks (DB, LLM, 1Password, memory)
  services/
    audit.py         - AuditService (log commands, search history)
    rate_limit.py    - RateLimiter (per-user, per-command rate limiting)
    memory.py        - MemoryService (store/search conversation memory)
    goals.py         - GoalService (CRUD, progress tracking)
    cron.py          - CronRunner (background scheduler, 60s check interval)
    digest.py        - DigestRunner (hourly check, daily/weekly digests)
    voice.py         - VoiceService (OpenAI Whisper transcription)
    ingestion.py     - IngestionService (URL fetch, file download, swarm analysis)
    rag.py           - RAGService (grep-based codebase search, file tree)
    metrics.py       - MetricsService (provider latency/success/quality tracking)
    triage.py        - TriageService (AI + keyword-based message classification)
    integrations.py  - IntegrationService (Linear, GitHub, Slack, Discord APIs)
    pipelines.py     - PipelineService (command chaining triggers)
    webhooks.py      - WebhookService (Telegram webhook mode setup)
```

## Architecture

### Cognitive Architecture (LLM OS)
The swarm operates under a full epistemic governance framework - the "Instructor Set" - that shapes how LLMs think, debate, and produce outputs.

#### Agent Constitution (constitution.py)
- Every claim must carry an epistemic label: VERIFIED, DERIVED, HYPOTHESIS, or UNKNOWN
- No blending categories; no rhetorical smoothing; if you can't label it, don't emit it
- Load-bearing claims (architecture, safety, cost) require evidence pointers
- Dual-mode reasoning: ANALYTIC (strict evidence) vs EXPLORATORY (hypothesis with test paths)
- Banned phrases without verification: "definitely", "guaranteed", "proven", "obviously", "clearly"
- Stop conditions: >50% unknown claims, constraint conflicts, policy blocks, consensus < 0.4
- Claim Ledger: every claim tracked with claim_id, confidence, evidence pointers, premises, failure modes

#### Compliance Scoring (compliance.py)
- Formula: CS = 100 - 8*U - 6*(L-E) - 12*C - 5*D - 7*M - 4*R - 6*X
  - U = unlabeled claims, L = load-bearing, E = evidenced, C = constraint violations
  - D = drift events, M = mode mixing, R = missing required fields, X = uncited external claims
- Tiers: TRUSTED (>=90), REDUCED (80-89), DRAFT (65-79), REJECTED (<65)
- Auto-correction protocol when CS < 80: agent must reissue with proper labels/evidence
- Effective weight = base_weight * (CS/100) * evidence_quality ("eloquent liars" become powerless)

#### Hallucination Risk Model (hallucination.py)
- Risk Score HR = 0.22*f1 + 0.22*f2 + 0.12*f3 + 0.10*f4 + 0.10*f5 + 0.10*f6 + 0.08*f7 + 0.06*f8
  - f1: unlabeled ratio, f2: unevidenced ratio, f3: mode mixing rate
  - f4: constraint pressure, f5: novelty pressure, f6: external dependency
  - f7: disagreement index, f8: overconfidence delta
- Tiers: LOW (<0.25, normal), MEDIUM (0.25-0.50, add skeptic), HIGH (0.50-0.75, block synthesis), CRITICAL (>0.75, halt)
- Predictive: throttles before hallucination occurs, not after
- HallucinationMonitor tracks trends and auto-aborts after 3 consecutive CRITICAL readings

#### Swarm Orchestration Contract (contract.py)
- 6 Cognitive Roles: Architect, Skeptic, Evidence Validator, Constraint Enforcer, Creative Divergence, Synthesizer
- 7-Round Debate Cycle:
  - Round 0: Genesis Binding (ensure intent, governance, capability plan)
  - Round 1: Proposal (architect claims with labels)
  - Round 2: Adversarial Audit (skeptic + constraint enforcer)
  - Round 3: Evidence Validation (verify pointers, score evidence quality)
  - Round 4: Creative Divergence (EXPLORATORY hypotheses with test paths)
  - Round 5: Consensus (weighted votes, 80% threshold, dissent preservation)
  - Round 6: Synthesis (final output from accepted claims only)
- Vote weight = base_role_weight * (CS/100) * evidence_strength * (1 - HR)
- Dissent preserved as metadata - innovation lives in minority disagreement
- Synthesizer fails rather than invents: no new claims, no unverified labels, no mode mixing

### Self-Healing Protocol
- Circuit breaker per LLM provider with 3 states (closed/open/half_open)
- Automatic provider isolation on repeated failures (threshold: 5)
- Recovery timeout with half-open probing (300s default)
- Background health monitor checking every 120s
- Graceful degradation: skips unhealthy providers

### 1Password Integration
- Service account token pulls API keys at startup via `op read`
- Keys loaded: OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, GROK_API_KEY, ASANA_PAT

### Porter Waves Swarm
- Wave 0 (Route+Clarify): identify projects/repos, constraints, permissions
- Wave 1 (Plan): architecture + task graph + acceptance criteria + risk
- Wave 2 (Build): implement diffs + tests; create branches + PRs
- Wave 3 (Verify+Ship): run checks; Terraform plan; staged deploy; smoke tests
- Wave 4 (Scale+Monetize): instrumentation, KPIs, cost controls, growth loops
- Cognitive roles mapped per wave: early waves use Architect/Skeptic/Constraint, later waves add all 6 roles

### Multi-Provider LLM
- Fans out to OpenAI + Anthropic + Gemini + Grok in parallel
- Compliance-weighted consensus: provider outputs scored by CS and HR before merge
- Judge/merger prompt includes compliance weighting instructions
- Critical HR triggers warnings in synthesis step

### Advanced Features (16 total)
1. Audit Logging - Every command logged with user, args, result; searchable via /audit
2. Rate Limiting - Per-user, per-command rate limits
3. Conversation Memory - Auto-stores results from /extract, voice, ingestion, RAG, goals
4. Goal Tracking - /goals, /goalstatus, /goalcomplete
5. Cron Scheduler - Background job runner with 60s check interval (/cron)
6. Recurring Digests - Daily/weekly summaries (/digest)
7. Voice Transcription - OpenAI Whisper (auto on voice messages)
8. Document Ingestion - URL fetch + file download with swarm analysis (/ingest)
9. RAG over Codebase - Grep-based code search + file tree (/askcode, /codestructure)
10. Provider Metrics - Latency, success rate, quality tracking (/metrics)
11. Auto-Triage - AI + keyword classification (/triage)
12. External Integrations - Linear, GitHub, Slack, Discord (/connect, /slack, /discord, /ghissue, /linearissue)
13. Pipeline Triggers - Command chaining automation (/pipeline)
14. Webhook Mode - BOT_MODE=webhook env var
15. Asana Integration - /extract auto-creates tasks
16. Content Analysis - Multi-provider swarm analysis (/extract)

### Security Model
- Capability Broker: agents request capabilities, broker enforces policy
- No raw secrets ever exposed to agents
- Production actions blocked by default
- Authorization with password + optional ADMIN_USER_ID

## Environment Variables
- `TELEGRAM_BOT_TOKEN` - Bot token from @BotFather (required)
- `ADMIN_PASSWORD` - Password for authorizing users (required)
- `ADMIN_USER_ID` - Optional Telegram user ID for extra auth security
- `DATABASE_URL` - PostgreSQL connection string (provided by Replit)
- `OP_SERVICE_ACCOUNT_TOKEN` - 1Password service account token
- `LLM_PROVIDER` - "stub" (default) or any other value to activate multi-provider LLM
- `BOT_MODE` - "polling" (default) or "webhook"
- `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` / `GROK_API_KEY` - LLM keys (auto-loaded from 1Password)
- `OPENAI_MODEL` / `ANTHROPIC_MODEL` / `GEMINI_MODEL` / `GROK_MODEL` - Model overrides
- `WAVE_CONCURRENCY` / `AGENTS_PER_WAVE` / `MAX_WAVES` / `PROVIDER_BUDGET_TOKENS` - Swarm tuning

## Dependencies
- python-telegram-bot==22.6
- SQLAlchemy>=2.0
- psycopg2-binary>=2.9
- openai, anthropic, google-generativeai
- httpx/aiohttp (for integrations)

## Running
The bot runs via `python main.py` using long polling by default. Background services (CronRunner at 60s, DigestRunner hourly, HealthMonitor at 120s) start automatically.

## Recent Changes
- 2026-02-16: Integrated cognitive architecture (Instructor Set) into swarm OS
  - Added Agent Constitution with epistemic categories and claim ledger discipline
  - Added Compliance Scoring Algorithm (CS 0-100) with 8-term penalty formula
  - Added Hallucination Risk Model (HR 0-1) with 8 weighted features and tiered controls
  - Added Swarm Orchestration Contract with 6 cognitive roles and 7-round debate cycle
  - Injected constitution rules into PORTER and WORKER system prompts
  - Wired compliance gating and HR throttling into WaveOrchestrator
  - Added compliance-weighted consensus to MultiProviderLLM judge/merge
  - Added /constitution and /cognition Telegram commands
- 2026-02-16: Integrated all 16 advanced features across 5 development phases
