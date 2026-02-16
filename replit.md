# Telegram Bot - Replit Manager + Porter Swarm

## Overview
This project is a Telegram bot designed to manage tasks, notes, shell commands, git operations, and multi-agent swarm orchestration within a Replit environment. Its core purpose is to provide a comprehensive, intelligent interface for project management and autonomous development, featuring advanced AI governance for enhanced reliability and compliance. The bot aims to streamline development workflows, automate complex tasks, and facilitate robust multi-agent collaboration with built-in safeguards against common LLM pitfalls like hallucination.

## User Preferences
The user prefers a structured, high-governance approach to AI interaction, emphasizing epistemic discipline and transparent decision-making. The user also values robust error handling, self-healing capabilities, and clear audit trails for all bot actions. Prioritizes the prevention of unverified claims and desires a system that actively identifies and mitigates risks associated with LLM outputs.

## System Architecture

### Cognitive Architecture (LLM OS)
The bot incorporates a sophisticated "Instructor Set" for epistemic governance, guiding how LLMs operate, debate, and produce results.

#### Agent Constitution
- **Epistemic Labels**: All claims must be labeled (VERIFIED, DERIVED, HYPOTHESIS, UNKNOWN).
- **Reasoning Modes**: Supports both ANALYTIC (evidence-based) and EXPLORATORY (hypothesis-driven) reasoning.
- **Banned Phrases**: Prohibits unverified definitive language.
- **Stop Conditions**: Implements conditions to halt processing based on uncertainty or policy conflicts.
- **Claim Ledger**: Tracks all claims with metadata like confidence, evidence, and potential failure modes.

#### Compliance Scoring (CS)
- **Formula-based Scoring**: A weighted formula calculates compliance (0-100) based on factors like unlabeled claims, constraint violations, and missing data.
- **Tiers**: Classifies outputs into TRUSTED, REDUCED, DRAFT, and REJECTED tiers.
- **Auto-correction**: Triggers automatic correction for low-scoring outputs.
- **Effective Weighting**: Adjusts agent influence based on compliance score and evidence quality.

#### Hallucination Risk Model (HR)
- **Risk Score**: A weighted model assesses hallucination risk (0-1) considering factors like unlabeled ratios, mode mixing, and overconfidence.
- **Tiers & Controls**: Defines tiers (LOW, MEDIUM, HIGH, CRITICAL) with corresponding control actions, including adding skeptics or halting synthesis.
- **Predictive**: Designed to prevent hallucinations proactively.

#### Swarm Orchestration Contract
- **Cognitive Roles**: Employs 9 distinct roles (e.g., Architect, Skeptic, Red Team) for comprehensive analysis.
- **Debate Cycle**: An 8-round debate process, from proposal to synthesis, ensures thorough vetting of claims.
- **Weighted Consensus**: Decisions are made via weighted voting, preserving minority dissent.
- **Synthesis Rules**: The synthesizer only produces outputs based on accepted, verified claims.

#### Provenance Tracking
- **Claim Provenance**: Tracks the origin and transformation lineage of every claim.
- **Provenance Tree**: Visualizes claim lineage and risk paths.
- **Consistency Checker**: Detects contradictions by cross-referencing verified facts.

#### Reflexive Auditor & Governance Sentinel
- **Meta-cognitive Monitoring**: Observes swarm performance, tracks trends, and generates system health assertions.
- **Real-time Risk Monitoring**: Provides warnings and throttles when risk thresholds are exceeded.
- **Red Team Agent**: Conducts adversarial vulnerability testing.

### Self-Healing Protocol
- **Circuit Breakers**: Implements per-LLM provider circuit breakers with closed/open/half_open states.
- **Provider Isolation**: Automatically isolates failing providers.
- **Health Monitor**: Background checks for database, LLM, and 1Password health.

### Porter Waves Swarm
- **Phased Execution**: Organizes complex tasks into sequential "waves" (Route+Clarify, Plan, Build, Verify+Ship, Scale+Monetize).
- **Role Mapping**: Assigns cognitive roles dynamically across different waves.

### Multi-Provider LLM
- **Parallel Execution**: Fans out queries to OpenAI, Anthropic, Gemini, and Grok.
- **Compliance-Weighted Consensus**: Merges provider outputs based on compliance and hallucination risk scores.

### Advanced Features
- **Audit Logging**: Comprehensive logging and search for all commands.
- **Rate Limiting**: Per-user and per-command rate limits.
- **Conversation Memory**: Stores key interactions and results.
- **Goal Tracking**: Manages and tracks project goals.
- **Cron Scheduler**: Background job execution.
- **Recurring Digests**: Provides scheduled summaries.
- **Voice Transcription**: Integrates OpenAI Whisper for voice messages.
- **Document Ingestion**: Fetches and analyzes content from URLs and files.
- **RAG over Codebase**: Enables grep-based code search and file tree analysis.
- **Provider Metrics**: Tracks performance of LLM providers.
- **Auto-Triage**: AI and keyword-based message classification.
- **External Integrations**: Connects with Linear, GitHub, Slack, Discord.
- **Pipeline Triggers**: Automates command chaining.
- **Webhook Mode**: Supports webhook deployment.
- **Asana Integration**: Auto-creates tasks in Asana from extracted content.
- **Content Analysis**: Utilizes multi-provider swarm for content analysis.

### Security Model
- **Capability Broker**: Controls agent access to capabilities based on policy.
- **Secret Protection**: Prevents raw secret exposure to agents.
- **Production Gates**: Blocks production actions by default.
- **Authorization**: Secure user authorization via password and optional ADMIN_USER_ID.

## External Dependencies
- **Telegram**: Main interaction platform.
- **Python-Telegram-Bot**: Bot API wrapper.
- **SQLAlchemy**: ORM for database interactions.
- **PostgreSQL**: Primary data store.
- **1Password**: Secure secret management for API keys (e.g., OpenAI, Anthropic, Gemini, Grok, Asana).
- **OpenAI, Anthropic, Google Generative AI**: LLM providers.
- **HTTPX/AIOHTTP**: For various external integrations.
- **Asana**: Task management integration.
- **Linear, GitHub, Slack, Discord**: Collaboration and issue tracking integrations.

## Project Structure
```
main.py              - Entry point (loads 1Password secrets before bot start)
src/
  op_secrets.py      - 1Password CLI integration for secret loading at startup
  bot.py             - Bot application setup, polling/webhook, health monitor, cron/digest init
  database.py        - SQLAlchemy engine, session, and Base
  models.py          - Database models (Task, Note, CommandLog, UserSettings, AuditLog,
                       ConversationMemory, Goal, CronJob, ProviderMetric, IntegrationConfig,
                       PipelineTrigger, DigestConfig, SwarmMemoryLayer, ClaimProvenanceRecord,
                       PerformanceLedgerEntry, SentinelEvent)
  handlers/          - Telegram command handlers (help, shell, tasks, notes, git, swarm, etc.)
  swarm/
    prompts.py       - System prompts + role prompts (9 roles + novelty preservation)
    constitution.py  - Agent Constitution: epistemic categories, claim ledger
    compliance.py    - CS scoring + novelty classification + consensus boost
    hallucination.py - HR model (0-1) with tiered controls
    contract.py      - 9 cognitive roles, 8-round debate cycle
    provenance.py    - ClaimProvenance tracking, ProvenanceTree, ConsistencyChecker
    auditor.py       - ReflexiveAuditor, PerformanceLedger, MetaClaim generation
    sentinel.py      - GovernanceSentinel, RedTeamAgent (5 attack vectors)
    orchestrator.py  - WaveOrchestrator with full governance wiring
    llm_multi.py     - MultiProviderLLM with novelty-aware consensus
    policy.py        - PolicyEngine (production gates, secret protection)
    broker.py        - CapabilityBroker (agents request, broker enforces)
  selfheal/          - Circuit breakers, health monitor
  services/          - Audit, rate limiting, memory, goals, cron, digest, voice, RAG, etc.
```

## Recent Changes
- 2026-02-16: Added perpetual consciousness - cognitive state persistence
  - CognitiveStatePersistence service saves/restores orchestrator state to DB
  - Provenance tree, auditor ledger, sentinel alerts persist across restarts
  - Rehydration on startup: orchestrator loads previous cognitive state from DB
  - Graceful shutdown handler persists state via post_shutdown hook
  - Deployment configured as Reserved VM for 24/7 always-on operation
- 2026-02-16: Enhanced cognitive architecture with v2.0 governance modules
  - Added provenance.py, auditor.py, sentinel.py
  - Enhanced compliance.py with NoveltyClassification and consensus_confidence_boost
  - Enhanced contract.py: 9 roles (added Reflexive Auditor, Red Team, Falsifier) + 8 debate rounds
  - Wired provenance/auditor/sentinel into orchestrator.py
  - Enhanced llm_multi.py with novelty-aware scoring
  - Added 4 new DB models
- 2026-02-16: Integrated cognitive architecture (Instructor Set) into swarm OS
- 2026-02-16: Integrated all 16 advanced features across 5 development phases