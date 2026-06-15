# MyDude.io - Business Automation Platform

## Overview
MyDude.io is a web-based AI business automation platform built with FastAPI. It provides a dark-themed dashboard for running AI tasks through a multi-provider LLM swarm with built-in compliance scoring and hallucination risk assessment. The platform features encrypted API key management, task history tracking, and a governance-first approach to AI outputs.

**Current Phase**: MVP (Phase 1) - Web dashboard with authentication, API key management, and AI task execution.
**Future Phases**: Web scraping, AWS integration, git operations, CRM, customer service, bookkeeping, email/phone/text automation, website generation, social media content, pool bid creation.

## User Preferences
- Project/product name is "MyDude.io" (canonical; deployed at mydude.io). The name lives in src/web/branding.py (single source of truth) — change it there to rename everywhere.
- Structured, high-governance approach to AI interaction
- Emphasis on epistemic discipline and transparent decision-making
- Robust error handling, self-healing capabilities, clear audit trails
- Prevention of unverified claims; risk mitigation for LLM outputs
- Dark theme UI, self-contained (no external CDN dependencies)
- **Developer access principle (permanent):** An access/beta gate must NEVER lock the developer out of the site in the dev/workspace environment. The login page shows a one-click "Developer sign-in" button when `REPLIT_DEPLOYMENT` is not `1`. That button and its backing endpoints (`/api/auth/dev-info`, `/api/auth/dev-login`) are hard-gated to return 403 / `available: false` when `REPLIT_DEPLOYMENT=1` — production stays fully locked. Never remove this affordance or weaken the production gate.

## Governance Pillars (HARD — apply perpetually, in planning AND building, dev AND prod)
These are non-negotiable foundational cores for MyDude.io and every sub-stack built on it. Honor them in every plan and every change; they also govern MyDude's own LLM swarm (cloud and self-hosted full-weight models on our VMs).
1. **No placeholders.** Never ship placeholder, mock, stub, or "TODO later" code. If a placeholder is unavoidable during development, it MUST be converted to its fully functional, operative implementation before the work is considered done. Fail loud rather than fake.
2. **Provider-agnostic code (separate provider from code).** Code must never be hardwired to a single provider. Abstract every external capability (LLM, finance, storage, etc.) behind an interface so providers are swappable without touching call sites.
3. **Separate provider from secrets.** Credentials are decoupled from both code and provider selection — sourced at runtime via the connector proxy first, then the vault/env fallback. Never hardcode or hand-handle raw secrets.
4. **Testing + function governance of inference.** Every inference path is tested and governed (compliance scoring, hallucination control, provenance, audit). No ungoverned model output reaches a user or an outbound action.
5. **Dynamic data schemas + DB stacks.** Prefer evolvable schemas and pluggable DB stacks over rigid hardcoding; tolerate schema growth (e.g. auto-migration) rather than brittle assumptions.
6. **Ultra 2026+ future-proofing.** Build forward-compatible: agnostic interfaces, versioned contracts, no dependence on a single vendor, model, or environment.

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
- 2026-06-15: Frontend first-load chunk splitting (removes the Vite >500 kB build warning)
  - The markdown renderer (streamdown, ~476 kB) is now lazy-loaded: its only consumer was moved to `frontend/src/components/ai-elements/message-response.tsx` (default export) and `React.lazy`-imported in `message.tsx`. This keeps streamdown off the eager dashboard/TaskDetail path (Dashboard chunk dropped to ~9.5 kB).
  - `livekit-client` (~506 kB) is a single irreducible pre-bundled vendor module, already lazy-loaded on the Avatar page; isolated into a named `livekit` chunk and covered by `chunkSizeWarningLimit: 520` in `frontend/vite.config.ts`.
  - Do NOT `maxSize`-split the streamdown/markdown ESM tree — it breaks module init order and silently blanks the page (see `.agents/memory/spa-chunk-splitting.md`).
- 2026-06-11: Dependency vulnerability remediation
  - starlette 0.52.1 → 1.3.0 + fastapi 0.131.0 → 0.136.3: fixes CVE-2026-48710 / GHSA-86qp-5c8j-p5mr / PYSEC-2026-161 ("BadHost" Host-header auth/routing bypass). fastapi was bumped because <0.133.1 pins `starlette<1.0.0`. App's direct starlette surface (StarletteHTTPException handler, SessionMiddleware) verified working under 1.x; `@app.on_event("startup")` still fires.
  - diskcache 5.6.3 (CVE-2025-69872 / GHSA-w8v5-vhqr-4h9v, unsafe pickle): **ACCEPTED RESIDUAL RISK** — no upstream patch exists (PyPI latest is the vulnerable 5.6.3) and every dspy release hard-requires diskcache>=5.6.0, so it cannot be upgraded or removed. Mitigated at runtime via `dspy.configure_cache(restrict_pickle=True)` in `src/promptopt/lm_bridge.py:harden_dspy_cache()` (idempotent; runs at module import AND app startup), which swaps DSPy's disk cache to a restricted unpickler (`RestrictedDisk`). Re-evaluate / drop the shim when diskcache or dspy ships a patched release.
  - Build/lock fixes required to re-lock: bounded `requires-python` to ">=3.11,<3.13" (avoids the optuna 3.13/3.14 resolution split); removed vestigial `optuna`/`huggingface-hub` → `pytorch-cpu` mappings from `[tool.uv.sources]` (inconsistent with the lock, which resolves both from PyPI, and nothing actually resolves from that index — it blocked the re-lock).
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
