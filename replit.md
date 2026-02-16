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
    utils.py         - safe_json_dumps, clamp_list
```

## Architecture

### Porter Waves Swarm
- Wave 0 (Route+Clarify): identify projects/repos, constraints, permissions
- Wave 1 (Plan): architecture + task graph + acceptance criteria + risk
- Wave 2 (Build): implement diffs + tests; create branches + PRs
- Wave 3 (Verify+Ship): run checks; Terraform plan; staged deploy; smoke tests
- Wave 4 (Scale+Monetize): instrumentation, KPIs, cost controls, growth loops

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
- `LLM_PROVIDER` - LLM provider: "stub" (default) or implement your own
- `WAVE_CONCURRENCY` - Max parallel agents per wave (default 12)
- `AGENTS_PER_WAVE` - Virtual agents per wave (default 60)
- `MAX_WAVES` - Maximum cascade waves (default 4)

## Dependencies
- python-telegram-bot==22.6
- SQLAlchemy>=2.0
- psycopg2-binary>=2.9

## Running
The bot runs via `python main.py` using long polling. Deployed as a VM for perpetual operation.

## Recent Changes
- Integrated Porter waves swarm architecture with cascading wave orchestration
- Added capability broker with policy engine for safe privileged operations
- Added integration stubs for Git, Terraform, Asana, 1Password
- LLM provider is stubbed; wire your provider in src/swarm/orchestrator.py LLM.call()
