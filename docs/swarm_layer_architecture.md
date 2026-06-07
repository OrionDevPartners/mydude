# Swarm Layer Architecture

## Layer Model: substrate → seam → bridge → contract → runtime

This document defines the five-layer governance substrate for the MyDude swarm engine,
maps every module to exactly one layer, and specifies the allowed call directions between them.

---

## Layer Definitions

| Layer | Role | Direction |
|-------|------|-----------|
| **substrate** | Foundations, shared state, DB, config, crypto | ← all layers read from here |
| **seam** | Typed data hand-off points between layers (dataclasses, schemas) | one-directional pass-through |
| **bridge** | Adapters that cross runtime boundaries (LLM providers, SSH, browser) | runtime → bridge → external |
| **contract** | Formal interaction contracts: cognitive roles, capability contracts, epistemic rules | enforced at every call boundary |
| **runtime** | Orchestration engine: wave execution, governance, auditing, self-healing | consumes all lower layers |

## Module Map

### substrate
- `src/database.py` — SQLAlchemy engine, session factory, column-sync migration
- `src/models.py` — All DB models (persistent state)
- `src/web/crypto.py` — Fernet encrypt/decrypt (shared crypto primitive)
- `src/swarm/utils.py` — Pure utility functions (clamping, JSON serialisation)

### seam
- `src/swarm/constitution.py` — Claim, ClaimLedger, EpistemicCategory, IntentBinding, StopCondition
- `src/swarm/contract.py` — CognitiveRole, DebateRound, AgentMessage, ConsensusResult, DissentRecord
- `src/swarm/prompts.py` — System prompts + role prompt templates (typed prompt seam)
- `src/swarm/capability_contracts.py` — CapabilityContract declarations (model↔tool contract seam)

### bridge
- `src/swarm/llm_multi.py` — MultiProviderLLM: fan-out to LLM provider APIs
- `src/swarm/integrations.py` — Git, Terraform, Asana, 1Password, browser, SSH integrations
- `src/browser/` — Browser engine adapters (Browserbase, Apify, AgentCore, Azure)
- `src/bridge/` — SSH and IMAP bridge adapters
- `src/providers/` — LLM provider adapters + config registry

### contract
- `src/swarm/compliance.py` — Compliance scoring, tier classification, novelty detection
- `src/swarm/hallucination.py` — Hallucination Risk Model, tiered controls
- `src/swarm/policy.py` — PolicyEngine: capability allow-lists, production gates
- `src/swarm/broker.py` — CapabilityBroker: contract-validate → policy-gate → integrate
- `src/swarm/jurisdiction.py` — exec_locus / cloud_shift routing policy

### runtime
- `src/swarm/orchestrator.py` — WaveOrchestrator: wave execution, consensus, run indexing
- `src/swarm/auditor.py` — ReflexiveAuditor → governance proposals (never silent mutation)
- `src/swarm/sentinel.py` — GovernanceSentinel, RedTeamAgent
- `src/swarm/provenance.py` — ProvenanceTree, ConsistencyChecker
- `src/swarm/governance_engine.py` — Governance proposals, voting, enactment (OpenGov pattern)
- `src/selfheal/` — Circuit breakers, health monitor

---

## Allowed Call Directions

```
external systems
      ↑
   bridge          (outbound only; no layer calls back through bridge)
      ↑
   runtime  ←→  contract  (runtime enforces contract; contract never calls runtime)
      ↑                ↑
    seam             seam      (seam is passed through; never initiates)
      ↑
  substrate         (everyone reads substrate; no layer writes to substrate except runtime/web)
```

**Rules:**
1. Higher layers may call lower layers; lower layers **never** call up.
2. `bridge` is called only by `runtime` (orchestrator/broker). No other layer reaches into bridge directly.
3. `contract` layer modules (policy, broker, compliance, hallucination) never import from `runtime`.
4. `seam` modules (constitution, contract dataclasses, prompts, capability_contracts) are pure data — no IO, no DB calls.
5. Every cross-layer call through the broker passes: **contract validation → policy gate → integration**.

---

## Cross-Layer Seam Points (single-responsibility)

| Seam ID | From | To | Carrier |
|---------|------|----|---------|
| S1 | runtime/orchestrator | bridge/llm_multi | `LLM.call()` with typed prompts |
| S2 | runtime/orchestrator | contract/broker | `CapabilityBroker.request(capability, params)` |
| S3 | contract/broker | bridge/integrations | `Integrations.*()` methods |
| S4 | contract/broker | contract/policy | `PolicyEngine.evaluate(capability, params)` |
| S5 | contract/broker | seam/capability_contracts | `validate_request(capability, params)` |
| S6 | runtime/auditor | runtime/governance_engine | `GovernanceEngine.from_meta_claim(claim)` |
| S7 | runtime/orchestrator | substrate/models | `SwarmRunIndex` written at run completion |
| S8 | runtime/sentinel | runtime/governance_engine | `GovernanceEngine.raise_proposal(origin="sentinel", ...)` |
