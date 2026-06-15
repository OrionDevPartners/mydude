# Capability Operator Run Book

**Unified Provider-Agnostic Capability Layer (v2)**

---

## Overview

MyDude's capability system follows a strict three-layer separation:

| Layer | Location | Purpose |
|-------|----------|---------|
| **env_1** | `config/providers.toml` (committed) | Maps capabilities → providers; declares secret NAMES |
| **env_2** | Replit Secrets / credential vault (never committed) | Holds secret VALUES |
| **Code** | `src/capabilities/` | Vendor-agnostic interfaces; never names a vendor |

**Rule**: swapping or adding a provider for any capability is a single `env_1` edit + adapter registration. No call-site changes.

---

## Capability Categories

| Category | env_1 Section | Backend Table | Description |
|----------|---------------|---------------|-------------|
| `llm` | `[llm]` | `[providers.*]` | Multi-provider LLM swarm |
| `browser` | `[browser]` | `[browserbackends.*]` | Browser automation |
| `database` | `[database]` | `[databasebackends.*]` | Relational DB |
| `vector_search` | `[vector_search]` | `[vectorbackends.*]` | Dense/lexical search |
| `knowledge_store` | `[knowledge_store]` | `[knowledgebackends.*]` | KG / semantic memory |
| `object_storage` | `[object_storage]` | `[storagebackends.*]` | File/blob storage |
| `secrets_vault` | `[secrets_vault]` | `[vaultbackends.*]` | Credential resolution |
| `realtime` | `[realtime]` | `[realtimebackends.*]` | Voice / telephony |
| `orchestrator` | `[orchestrator]` | `[orchestratorbackends.*]` | Cognitive orchestrator |
| `sig_optimizer` | `[sig_optimizer]` | `[optimizerbackends.*]` | Prompt optimizer |
| `container_compute` | `[container_compute]` | `[computebackends.*]` | Subprocess / compute |

---

## env_1 / env_2 Reference

### LLM

**env_1 keys** (`config/providers.toml`):
```toml
[llm]
enabled = ["openai", "anthropic", "gemini", "grok", "deepseek", "mistral", "qwen", "ollama", "mlx"]
required = []

[providers.openai]
adapter = "openai_chat"
secrets = ["OPENAI_API_KEY"]   # name only, value in env_2
model_env = "OPENAI_MODEL"
default_model = "gpt-5.5"
exec_locus = "in_azure"
```

**env_2 secrets** (Replit Secrets / vault):
| Provider | Secret Name | Notes |
|----------|-------------|-------|
| openai | `OPENAI_API_KEY` | |
| anthropic | `ANTHROPIC_API_KEY` | |
| gemini | `google_ai_studio` | Primary; `GEMINI_API_KEY` is fallback |
| grok | `GROK_API_KEY` | |
| deepseek | `DEEPSEEK_API_KEY` | |
| mistral | `MISTRAL_API_KEY` | |
| qwen | `DASHSCOPE_API_KEY` | |
| ollama | *(none)* | Local server; no secret |
| mlx | *(none)* | Local server; no secret |

---

### Browser

**env_1 keys**:
```toml
[browser]
enabled = ["local", "browserbase", "apify", "agentcore", "azure"]
required = []
```

**env_2 secrets**:
| Backend | Secret Names |
|---------|-------------|
| local_playwright | *(none)* |
| browserbase | `BROWSERBASE_API_KEY`, `BROWSERBASE_PROJECT_ID` |
| apify | `APIFY_API_TOKEN` |
| agentcore | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` |
| azure | `AZURE_PLAYWRIGHT_ACCESS_TOKEN`, `PLAYWRIGHT_SERVICE_URL` |

---

### Database

**env_1 keys**:
```toml
[database]
enabled = ["postgresql"]
required = []

[databasebackends.postgresql]
adapter = "postgresql"
secrets = []
exec_locus = "local"
```

**env_2 secrets**: `DATABASE_URL` — injected automatically by the Replit platform (built-in PostgreSQL). No manual secret needed.

---

### Vector Search

**env_1 keys**:
```toml
[vector_search]
enabled = ["tfidf", "embedding"]
required = []
```

**Configuration** (env_2 / environment settings):
| Variable | Purpose |
|----------|---------|
| `EMBEDDING_MODEL` | Model ID (e.g. `nomic-embed-text`, `all-MiniLM-L6-v2`) |
| `EMBEDDING_PROVIDER` | `ollama` \| `mlx` \| `openai` \| `sentence-transformers` |
| `EMBEDDING_BASE_URL` | Override the OpenAI-compatible endpoint |
| `EMBEDDING_API_KEY_ENV` | NAME of the secret holding the cloud embedding key |
| `EMBEDDING_EXEC_LOCUS` | `local` \| `cloud` |

TF-IDF (`tfidf`) requires no configuration and is always available.

---

### Knowledge Store

**env_1 keys**:
```toml
[knowledge_store]
enabled = ["cognee", "mem0"]
required = []
```

- `cognee`: Vendored in-process knowledge graph. No secrets. Degrades gracefully if Cognee package is unavailable.
- `mem0`: Cloud semantic memory. No built-in secrets required (Mem0 may require its own API key; configure via the vault if using a cloud Mem0 endpoint).

---

### Object Storage

**env_1 keys**:
```toml
[object_storage]
enabled = ["memory", "local_fs", "db_store"]
required = []
```

- `memory`: In-process ephemeral; cleared on restart. No config.
- `local_fs`: Filesystem. Path: `LOCAL_STORAGE_PATH` env var (default `/tmp/mydude_storage`).
- `db_store`: Database-backed. Requires the database to be reachable.

---

### Secrets Vault

**env_1 keys**:
```toml
[secrets_vault]
enabled = ["connector_proxy", "env_vault"]
required = []
```

- `connector_proxy`: Replit OAuth/integration proxy. Available when `REPLIT_CONNECTORS_HOSTNAME` + identity token are present.
- `env_vault`: Process environment (Replit Secrets / vault sync target). Always available.

---

### Realtime / Telephony

**env_1 keys**:
```toml
[realtime]
enabled = ["twilio"]
required = []
```

**env_2 secrets**:
| Secret | Notes |
|--------|-------|
| `TWILIO_ACCOUNT_SID` | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | Twilio auth token |
| `TWILIO_PHONE_NUMBER` | Optional default caller-ID |

---

### Orchestrator

**env_1 keys**:
```toml
[orchestrator]
enabled = ["wave_orchestrator"]
required = []
```

No secrets. Availability is derived from the LLM layer — available when any LLM provider is reachable.

---

### Signature Optimizer

**env_1 keys**:
```toml
[sig_optimizer]
enabled = ["dspy_bridge"]
required = []
```

No secrets. Requires the `dspy` package and at least one live LLM provider.

---

### Container Compute

**env_1 keys**:
```toml
[container_compute]
enabled = ["subprocess_local"]
required = []
```

No secrets. Always available (subprocess is a Python built-in). Command allow-list enforcement is the `CapabilityBroker`/`PolicyEngine`'s responsibility.

---

## How to Swap a Provider (Zero Code Change)

### Example: Switch to a new LLM provider

1. **Add the secret** (env_2): In Replit Secrets or the credential vault, add `NEW_PROVIDER_API_KEY = <value>`.

2. **Edit env_1** (`config/providers.toml`):
   ```toml
   [providers.new_provider]
   adapter = "openai_chat"          # reuse existing adapter if OpenAI-compatible
   secrets = ["NEW_PROVIDER_API_KEY"]
   default_model = "new-model-name"
   exec_locus = "provider_hosted"
   
   [llm]
   enabled = ["new_provider", "openai", ...]
   ```

3. **Register the adapter** (only if a new adapter class is needed): Add an entry to `src/capabilities/registry.py` → `CAPABILITY_REGISTRY`.

4. **Reload** (no cold restart needed): Call `POST /api/capabilities/reload` or restart the app.

5. **Verify**: Call `GET /api/capabilities/swap-test?category=llm&key=new_provider` — returns `{"ok": true, ...}` when the swap is live.

### Example: Switch the database backend

1. Edit `[database].enabled` in `config/providers.toml` to add the new backend key.
2. Add a `[databasebackends.<key>]` block with `adapter`, `secrets`, and `exec_locus`.
3. Implement the adapter class (extends `CapabilityAdapter`) and register it in `CAPABILITY_REGISTRY`.
4. Add the required secrets in env_2.
5. Reload and verify via the API.

---

## How to Add a New Capability Category

1. **Define the category** in `config/providers.toml`:
   ```toml
   [my_new_category]
   enabled = ["my_provider"]
   required = []

   [mynewcategorybackends.my_provider]
   adapter = "my_adapter"
   secrets = ["MY_PROVIDER_API_KEY"]
   exec_locus = "provider_hosted"
   label = "My New Provider"
   ```

2. **Add to `ALL_CATEGORIES`** in `src/capabilities/config.py` and the backend-table map `_BACKEND_TABLE`.

3. **Implement the adapter class** in a new file `src/capabilities/adapters/my_new_category.py` extending `CapabilityAdapter`. Implement `_probe()` and optionally `health_probe()`.

4. **Register the adapter** in `src/capabilities/registry.py` → `CAPABILITY_REGISTRY`:
   ```python
   ("my_new_category", "my_adapter"): MyNewAdapter,
   ```

5. **Run the handshake** (`run_unified_handshake()`) at startup — it automatically validates the new category.

---

## Live Diagnostic Commands

```
# Full capability matrix
GET /api/capabilities/matrix

# Single category status
GET /api/capabilities/category/database

# Swap self-test (zero code change proof)
GET /api/capabilities/swap-test?category=realtime&key=twilio

# Force reload after config change (no cold restart)
POST /api/capabilities/reload
```

---

## Boot Handshake Behavior

The unified handshake (`src/capabilities/handshake.py`) runs at startup for every category:

- **Config integrity**: every enabled/required key must have a `[<category>backends.<key>]` definition AND a registered adapter class.
- **Secret validation**: every key in `[<category>].required` must have all its declared secrets present in env_2.
- **Fail-loud**: any error raises `CapabilityHandshakeError` with a specific, actionable message before the app serves traffic.
- **LLM/browser**: validated through their original handshake modules (behavior preserved exactly).

To make a provider required (must have secrets at boot):
```toml
[realtime]
enabled = ["twilio"]
required = ["twilio"]   # boot fails if TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN are absent
```

---

## Governance Pillars Applied

| Pillar | How it applies |
|--------|---------------|
| #1 No placeholders | Every adapter wraps a real, operative implementation |
| #2 Provider-agnostic code | Call sites name a category, never a vendor |
| #3 Separate provider from secrets | Secrets resolved by NAME from env_2 at runtime |
| #4 Governed inference | Orchestrator/LLM paths unchanged; governance scoring preserved |
| #5 Evolvable schemas | `CapabilitySpec.extra` absorbs new config fields without schema changes |
| #6 Forward-compatible | Adding a provider = one config block + one adapter class |
