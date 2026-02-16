# Telegram Bot - Replit Manager + Porter Swarm

## Overview
A Telegram bot for managing tasks, notes, shell commands, git operations, and multi-agent swarm orchestration on a Replit project. Built with Python 3.11, python-telegram-bot v22, SQLAlchemy, and PostgreSQL. Features a self-healing protocol with circuit breakers, health monitoring, automatic secret loading from 1Password, and 16 advanced features including conversation memory, recurring digests, document ingestion, auto-triage, pipeline triggers, cron jobs, external integrations, voice transcription, RAG, provider metrics, goal tracking, webhook mode, rate limiting, and audit logging.

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
  swarm/
    prompts.py       - PORTER_SYSTEM_PROMPT and WORKER_SYSTEM_PROMPT
    policy.py        - PolicyEngine (production gates, secret protection)
    broker.py        - CapabilityBroker (agents request, broker enforces + executes)
    integrations.py  - Safe integration layer (git, terraform, asana, 1password stubs)
    orchestrator.py  - WaveOrchestrator + LLM + Handoff compression
    llm_multi.py     - MultiProviderLLM (OpenAI, Anthropic, Gemini, Grok fanout + judge merge)
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

### Self-Healing Protocol
- Circuit breaker per LLM provider with 3 states (closed/open/half_open)
- Automatic provider isolation on repeated failures (threshold: 5)
- Recovery timeout with half-open probing (300s default)
- Background health monitor checking every 120s: DB connectivity, LLM provider health, 1Password status, memory usage
- Graceful degradation: skips unhealthy providers, runs with whatever is available
- Admin alerts via Telegram when critical components fail
- Commands: /selfheal (status), /healcheck (trigger immediate check)

### 1Password Integration
- Service account token pulls API keys at startup via `op read`
- No LLM keys stored as Replit secrets - all pulled fresh from vault
- Keys loaded: OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, GROK_API_KEY, ASANA_PAT
- Automatic CLI download if not present

### Porter Waves Swarm
- Wave 0 (Route+Clarify): identify projects/repos, constraints, permissions
- Wave 1 (Plan): architecture + task graph + acceptance criteria + risk
- Wave 2 (Build): implement diffs + tests; create branches + PRs
- Wave 3 (Verify+Ship): run checks; Terraform plan; staged deploy; smoke tests
- Wave 4 (Scale+Monetize): instrumentation, KPIs, cost controls, growth loops

### Multi-Provider LLM
- Fans out to OpenAI + Anthropic + Gemini + Grok in parallel
- Circuit breaker integration: unhealthy providers auto-skipped
- Per-provider concurrency caps and retry with backoff
- Judge/merger step synthesizes outputs into single handoff
- Model resolver auto-detects latest chat model per provider (cached with TTL)
- Set LLM_PROVIDER=multi (any non-"stub" value) to activate; default is "stub"

### Advanced Features (16 total)
1. **Audit Logging** - Every command logged with user, args, result; searchable via /audit
2. **Rate Limiting** - Per-user, per-command rate limits to prevent abuse
3. **Conversation Memory** - Auto-stores results from /extract, voice, ingestion, RAG, goals
4. **Goal Tracking** - Create/track/complete goals with progress monitoring (/goals, /goalstatus, /goalcomplete)
5. **Cron Scheduler** - Background job runner with 60s check interval (/cron add/toggle/delete/run)
6. **Recurring Digests** - Daily/weekly summaries of tasks, notes, goals (/digest now/daily/weekly/toggle)
7. **Voice Transcription** - OpenAI Whisper for voice note transcription (auto on voice messages)
8. **Document Ingestion** - URL fetch + file download with swarm analysis (/ingest, document handler)
9. **RAG over Codebase** - Grep-based code search + file tree (/askcode, /codestructure)
10. **Provider Metrics** - Latency, success rate, quality tracking with dynamic weighting (/metrics)
11. **Auto-Triage** - AI + keyword classification of message urgency (/triage)
12. **External Integrations** - Linear, GitHub, Slack, Discord webhooks (/connect, /slack, /discord, /ghissue, /linearissue)
13. **Pipeline Triggers** - Command chaining automation (/pipeline add/toggle/delete)
14. **Webhook Mode** - Alternative to polling via BOT_MODE=webhook env var
15. **Asana Integration** - /extract auto-creates tasks, /setproject sets target project
16. **Content Analysis** - Multi-provider swarm analysis of pasted text (/extract)

### Security Model
- Capability Broker pattern: agents request capabilities, broker enforces policy
- No raw secrets ever exposed to agents
- Production actions blocked by default (toggle with /policy)
- Authorization with password + optional ADMIN_USER_ID restriction
- Rate limiting on authorization attempts and all commands

## Environment Variables
- `TELEGRAM_BOT_TOKEN` - Bot token from @BotFather (required)
- `ADMIN_PASSWORD` - Password for authorizing users (required)
- `ADMIN_USER_ID` - Optional Telegram user ID for extra auth security
- `DATABASE_URL` - PostgreSQL connection string (provided by Replit)
- `OP_SERVICE_ACCOUNT_TOKEN` - 1Password service account token (loads LLM keys from vault)
- `LLM_PROVIDER` - "stub" (default) or any other value to activate multi-provider LLM
- `BOT_MODE` - "polling" (default) or "webhook" for webhook mode
- `OPENAI_API_KEY` - OpenAI API key (auto-loaded from 1Password if not set)
- `ANTHROPIC_API_KEY` - Anthropic API key (auto-loaded from 1Password if not set)
- `GEMINI_API_KEY` - Google AI Studio key (auto-loaded from 1Password if not set)
- `GROK_API_KEY` - xAI/Grok API key (auto-loaded from 1Password if not set)
- `GROK_BASE_URL` - Grok API base URL (default: https://api.x.ai/v1)
- `OPENAI_MODEL` - Override OpenAI model (default: gpt-4.1-mini)
- `ANTHROPIC_MODEL` - Override Anthropic model (default: claude-sonnet-4-20250514)
- `GEMINI_MODEL` - Override Gemini model (default: gemini-2.0-flash)
- `GROK_MODEL` - Override Grok model (default: grok-2-latest)
- `WAVE_CONCURRENCY` - Max parallel agents per wave (default 12)
- `AGENTS_PER_WAVE` - Virtual agents per wave (default 60)
- `MAX_WAVES` - Maximum cascade waves (default 4)
- `PROVIDER_BUDGET_TOKENS` - Max tokens per provider call (default 1200)

## Dependencies
- python-telegram-bot==22.6
- SQLAlchemy>=2.0
- psycopg2-binary>=2.9
- openai
- anthropic
- google-generativeai
- httpx/aiohttp (for integrations)

## Running
The bot runs via `python main.py` using long polling by default. Set BOT_MODE=webhook for webhook mode. Deployed as a VM for perpetual operation. Background services (CronRunner at 60s, DigestRunner hourly, HealthMonitor at 120s) start automatically.

## Recent Changes
- 2026-02-16: Integrated all 16 advanced features across 5 development phases
- Added 8 new database models and 13 new service modules
- Created 12 new handler modules for all feature commands
- Implemented background runners: CronRunner (60s), DigestRunner (hourly)
- Updated /help with comprehensive command listing (80+ commands)
- All handlers registered in bot.py with background services starting in _post_init
