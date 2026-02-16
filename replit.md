# Telegram Bot - Replit Manager + Porter Swarm

## Overview
A Telegram bot for managing tasks, notes, shell commands, git operations, and multi-agent swarm orchestration on a Replit project. Built with Python 3.11, python-telegram-bot v22, SQLAlchemy, and PostgreSQL. Features a self-healing protocol with circuit breakers, health monitoring, and automatic secret loading from 1Password.

## Project Structure
```
main.py              - Entry point (loads 1Password secrets before bot start)
src/
  op_secrets.py      - 1Password CLI integration for secret loading at startup
  bot.py             - Bot application setup, polling, and health monitor init
  database.py        - SQLAlchemy engine, session, and Base
  models.py          - Database models (Task, Note, CommandLog, UserSettings)
  handlers/
    help.py          - /start, /help, /authorize, /whoami
    shell.py         - /shell command execution
    tasks.py         - /addtask, /tasks, /donetask, /deltask
    notes.py         - /addnote, /notes, /viewnote, /delnote
    git.py           - /gitstatus, /gitlog, /gitdiff, /gitcommit, /gitpull, /gitpush
    swarm.py         - /goal, /waves, /policy (Porter swarm commands)
    selfheal.py      - /selfheal, /healcheck (self-healing protocol status)
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
- Keys loaded: OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, GROK_API_KEY
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

### Security Model
- Capability Broker pattern: agents request capabilities, broker enforces policy
- No raw secrets ever exposed to agents
- Production actions blocked by default (toggle with /policy)
- Authorization with password + optional ADMIN_USER_ID restriction
- Rate limiting on authorization attempts

## Environment Variables
- `TELEGRAM_BOT_TOKEN` - Bot token from @BotFather (required)
- `ADMIN_PASSWORD` - Password for authorizing users (required)
- `ADMIN_USER_ID` - Optional Telegram user ID for extra auth security
- `DATABASE_URL` - PostgreSQL connection string (provided by Replit)
- `OP_SERVICE_ACCOUNT_TOKEN` - 1Password service account token (loads LLM keys from vault)
- `LLM_PROVIDER` - "stub" (default) or any other value to activate multi-provider LLM
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

## Running
The bot runs via `python main.py` using long polling. Deployed as a VM for perpetual operation.

## Recent Changes
- Implemented self-healing protocol: circuit breakers, health monitor, /selfheal and /healcheck commands
- Added 1Password integration for automatic LLM API key loading at startup
- Fixed OpenAI model resolver to avoid non-chat models (codex)
- Tested 4-provider weighted consensus: Anthropic, Gemini, Grok all responded successfully
- LLM_PROVIDER set to "multi" for active multi-provider mode
