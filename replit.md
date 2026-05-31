# BoBot AI - Business Automation Platform

## Overview
BoBot AI is a web-based AI business automation platform built with FastAPI. It provides a dark-themed dashboard for running AI tasks through a multi-provider LLM swarm with built-in compliance scoring and hallucination risk assessment. The platform features encrypted API key management, task history tracking, and a governance-first approach to AI outputs.

**Current Phase**: MVP (Phase 1) - Web dashboard with authentication, API key management, and AI task execution.
**Future Phases**: Web scraping, AWS integration, git operations, CRM, customer service, bookkeeping, email/phone/text automation, website generation, social media content, pool bid creation.

## User Preferences
- Structured, high-governance approach to AI interaction
- Emphasis on epistemic discipline and transparent decision-making
- Robust error handling, self-healing capabilities, clear audit trails
- Prevention of unverified claims; risk mitigation for LLM outputs
- Dark theme UI, self-contained (no external CDN dependencies)

## Tech Stack
- **Backend**: FastAPI + Uvicorn (Python 3.11)
- **Frontend**: Jinja2 templates, no JavaScript frameworks, system fonts
- **Database**: PostgreSQL (Replit built-in)
- **ORM**: SQLAlchemy
- **Auth**: Password-based session auth with itsdangerous
- **Encryption**: Fernet (cryptography library) for API key storage
- **AI Engine**: Multi-provider LLM swarm (OpenAI, Anthropic, Gemini, Grok)
- **Port**: 5000 (bound to 0.0.0.0)

## Project Structure
```
main.py                    - Entry point (uvicorn on 0.0.0.0:5000)
src/
  database.py              - SQLAlchemy engine, session, Base
  models.py                - All DB models (Task, Note, ApiKey, TaskRun, etc.)
  web/
    __init__.py
    app.py                 - FastAPI app setup, middleware, routers, startup events
    auth.py                - Login/logout routes, session management, require_auth dependency
    crypto.py              - Fernet encrypt/decrypt, key masking
    routes_keys.py         - API key CRUD (add, list, toggle, delete)
    routes_tasks.py        - Dashboard, task runner, history, detail views
  swarm/
    prompts.py             - System prompts + role prompts (9 roles)
    constitution.py        - Agent Constitution: epistemic categories, claim ledger
    compliance.py          - CS scoring + novelty classification
    hallucination.py       - HR model (0-1) with tiered controls
    contract.py            - 9 cognitive roles, 8-round debate cycle
    provenance.py          - ClaimProvenance tracking, ProvenanceTree
    auditor.py             - ReflexiveAuditor, PerformanceLedger
    sentinel.py            - GovernanceSentinel, RedTeamAgent
    orchestrator.py        - WaveOrchestrator with full governance wiring
    llm_multi.py           - MultiProviderLLM with novelty-aware consensus
    policy.py              - PolicyEngine (production gates, secret protection)
    broker.py              - CapabilityBroker (agents request, broker enforces)
    integrations.py        - Git/Terraform integration capabilities
  selfheal/                - Circuit breakers, health monitor
  services/                - Audit, rate limiting, memory, goals, cron, digest, etc.
templates/
  base.html                - Dark theme layout with sidebar navigation
  login.html               - Login page
  dashboard.html           - Task input form + results display
  keys.html                - API key management
  history.html             - Task history with pagination
  task_detail.html         - Full task result view
static/
  css/style.css            - Dark theme styles (#1a1a2e, #e94560 accents)
```

## Authentication
- Default password: "admin" (configurable via ADMIN_PASSWORD secret)
- Session-based auth with signed cookies (itsdangerous)
- All routes except /login and /health require authentication

## API Key Management
- Keys encrypted with Fernet before database storage
- Auto-generated ENCRYPTION_KEY env var if not set
- Keys synced to environment variables on startup for LLM providers
- Provider mapping: openai→OPENAI_API_KEY, anthropic→ANTHROPIC_API_KEY, gemini→GEMINI_API_KEY, grok→GROK_API_KEY

## AI Engine (Preserved from original)
- WaveOrchestrator: Phased execution across 5 waves
- Multi-provider LLM with compliance-weighted consensus
- 9 cognitive roles in 8-round debate cycles
- Hallucination Risk Model with tiered controls
- Provenance tracking, reflexive auditing, governance sentinel
- Circuit breakers and self-healing for provider isolation

## Recent Changes
- 2026-05-31: Upgraded API key management into a full credential vault
  - API Vault (/keys): any service, categories, search, expiry/rotation reminders, per-key env var, notes, reveal (Cache-Control: no-store), rotation, usage audit log (/keys/audit)
  - Connected Services (/connected): live Replit integration status via the connector proxy at runtime (src/web/connectors.py)
  - Service Directory (/directory): guided signup helper with provider links + step-by-step, pre-fills the vault add form
  - New: src/web/service_catalog.py (curated services), src/web/routes_services.py; ApiKey extended (category, env_var, notes, expires_at, rotation_days, last_used_at, last_rotated_at); new KeyAuditLog model (auto-migrated via _sync_missing_columns)
- 2026-02-23: Transformed from Telegram bot to FastAPI web application (Phase 1 MVP)
  - Built complete web dashboard with dark theme UI
  - Added authentication, API key management, AI task runner
  - Removed all Telegram bot code (handlers, bot.py, op_secrets.py)
  - Preserved AI engine (swarm, selfheal, services)
  - New models: ApiKey (encrypted storage), TaskRun (task history)
  - Deployment: FastAPI + Uvicorn on port 5000
