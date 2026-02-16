# Telegram Bot - Replit Manager + Porter Swarm

## Overview
A Telegram bot for managing tasks, notes, shell commands, git operations, and multi-agent swarm orchestration on a Replit project. Built with Python 3.11, python-telegram-bot v22, SQLAlchemy, and PostgreSQL.

## Project Structure
```
main.py              - Entry point
src/
  bot.py             - Bot application setup and polling
  database.py        - SQLAlchemy engine, session, and Base
  models.py          - Database models (Task, Note, CommandLog, UserSettings)
  handlers/
    help.py          - /start, /help, /authorize, /whoami
    shell.py         - /shell command execution
    tasks.py         - /addtask, /tasks, /donetask, /deltask
    notes.py         - /addnote, /notes, /viewnote, /delnote
    git.py           - /gitstatus, /gitlog, /gitdiff, /gitcommit, /gitpull, /gitpush
    swarm.py         - /goal, /waves, /policy (Porter swarm commands)
  swarm/
    prompts.py       - PORTER_SYSTEM_PROMPT and WORKER_SYSTEM_PROMPT
    policy.py        - PolicyEngine (production gates, secret protection)
    broker.py        - CapabilityBroker (agents request, broker enforces + executes)
    integrations.py  - Safe integration layer (git, terraform, asana, 1password stubs)
    orchestrator.py  - WaveOrchestrator + LLM + Handoff compression
    llm_multi.py     - MultiProviderLLM (OpenAI, Anthropic, Gemini, Grok fanout + judge merge)
    model_resolver.py - Auto-resolve latest model per provider family
    utils.py         - safe_json_dumps, clamp_list
```

## Architecture

### Porter Waves Swarm
- Wave 0 (Route+Clarify): identify projects/repos, constraints, permissions
- Wave 1 (Plan): architecture + task graph + acceptance criteria + risk
- Wave 2 (Build): implement diffs + tests; create branches + PRs
- Wave 3 (Verify+Ship): run checks; Terraform plan; staged deploy; smoke tests
- Wave 4 (Scale+Monetize): instrumentation, KPIs, cost controls, growth loops

### Multi-Provider LLM
- Fans out to OpenAI + Anthropic + Gemini + Grok in parallel
- Per-provider concurrency caps and retry with backoff
- Judge/merger step synthesizes outputs into single handoff
- Model resolver auto-detects latest model per provider family (cached with TTL)
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
- `LLM_PROVIDER` - "stub" (default) or any other value to activate multi-provider LLM
- `OPENAI_API_KEY` - OpenAI API key (optional, for multi-provider)
- `ANTHROPIC_API_KEY` - Anthropic API key (optional, for multi-provider)
- `GEMINI_API_KEY` - Google AI Studio key (optional, for multi-provider)
- `GROK_API_KEY` - xAI/Grok API key (optional, for multi-provider)
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
- Added multi-provider LLM with debate + weighted consensus + judge merge
- Added model resolver for auto-detecting latest models per provider
- Per-provider concurrency caps and exponential backoff retries
- Integrated into WaveOrchestrator: set LLM_PROVIDER to any non-"stub" value to activate
