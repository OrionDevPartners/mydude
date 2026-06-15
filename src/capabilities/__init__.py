"""Unified Provider-Agnostic Capability Layer (v2).

Single entry point through which ALL code resolves ANY capability provider.

Three-layer separation honored for every category:
  * Code (this package's interfaces + the call sites) never names a vendor.
  * env_1 (config/providers.toml, committed) maps capability -> provider and
    declares which secret NAMES each provider needs.
  * env_2 (Replit Secrets / the credential vault, never committed) holds the
    secret VALUES, read only through src.providers.secrets.

Capability categories
---------------------
  llm              Multi-provider LLM swarm (folded in from src/providers/)
  browser          Browser automation backends (folded in from src/browser/)
  database         Relational database (PostgreSQL via SQLAlchemy)
  vector_search    Dense vector / embedding search
  knowledge_store  Knowledge graph + semantic memory (Cognee / Mem0)
  object_storage   File / blob / document storage
  secrets_vault    Credential resolution (connector proxy + encrypted vault)
  realtime         Voice / telephony (Twilio facade)
  orchestrator     Cognitive wave orchestrator (WaveOrchestrator)
  sig_optimizer    Signature / prompt optimizer (DSPy bridge)
  container_compute  Local subprocess / container execution

Usage
-----
  from src.capabilities.resolver import get_resolver
  resolver = get_resolver()
  adapter = resolver.resolve("database")          # → DatabaseAdapter
  adapter = resolver.resolve("realtime")          # → TwilioRealtimeAdapter
  matrix = resolver.capability_matrix()           # full status dict for UI
"""
