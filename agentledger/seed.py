"""Populate the Agent Ledger from REAL project state.

Everything written here is derived from the actual repository — never invented:
  - packages      : parsed from pyproject.toml + frontend/package.json
  - containers    : scanned from the real filesystem (src/*, frontend, infra)
  - functions     : extracted via `ast` from each container's source
  - placements    : derived from a real `ast` import scan (which module imports what)
  - providers     : a curated catalog, each VERIFIED to appear in the source tree

Idempotent: rebuilds the non-audit schema from scratch every run so the ledger
always mirrors current reality. The append-only ``ledger_events`` audit log is
PRESERVED across rebuilds, so a lasting history of every reseed accumulates over
time (view it with ``python -m agentledger.query events``).
Run with:  python -m agentledger.seed
"""
from __future__ import annotations

import ast
import json
import os
import re
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from agentledger.db import SessionLocal, init_ledger
from agentledger.models import (
    Capability,
    ComponentDependency,
    Container,
    Function,
    Layer,
    LedgerEvent,
    Package,
    Placement,
    Provider,
    ProviderCapability,
    SecretRequirement,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Layer taxonomy (curated from the real project architecture in replit.md)
# ---------------------------------------------------------------------------

LAYERS: List[dict] = [
    {"slug": "interface", "name": "Interface", "kind": "interface", "order_index": 10,
     "description": "User-facing surfaces: React SPA + FastAPI HTTP/JSON layer."},
    {"slug": "runtime", "name": "Cognition Runtime", "kind": "runtime", "order_index": 20,
     "description": "The governed cognition spine: Cogitation entrypoint, WaveOrchestrator, prompt evolution, bot fleet."},
    {"slug": "memory", "name": "Knowledge Plane", "kind": "memory", "order_index": 30,
     "description": "Semantic + graph memory substrate (Cognee local KG + Mem0 cloud)."},
    {"slug": "providers", "name": "Provider & Connectivity", "kind": "providers", "order_index": 40,
     "description": "Provider-agnostic adapters, secret sourcing, remote connectivity."},
    {"slug": "domain", "name": "Business Domains", "kind": "domain", "order_index": 50,
     "description": "Vertical sub-stacks: coach, finance, browser, avatar, subscriptions."},
    {"slug": "resilience", "name": "Resilience", "kind": "resilience", "order_index": 60,
     "description": "Self-healing, circuit breakers, health monitoring, background services."},
    {"slug": "data", "name": "Data Core", "kind": "data", "order_index": 70,
     "description": "ORM models, DB engine, app entrypoint."},
    {"slug": "infra", "name": "Infrastructure", "kind": "infra", "order_index": 80,
     "description": "Jurisdiction routing, deployment/provisioning topology."},
]

# Real src/ dirs -> layer slug. (dirs absent on disk are skipped.)
CONTAINER_LAYER: Dict[str, str] = {
    "src/web": "interface",
    "src/swarm": "runtime",
    "src/promptopt": "runtime",
    "src/fleet": "runtime",
    "src/memory": "memory",
    "src/vendors": "memory",
    "src/providers": "providers",
    "src/bridge": "providers",
    "src/coach": "domain",
    "src/finance": "domain",
    "src/browser": "domain",
    "src/avatar": "domain",
    "src/subscriptions": "domain",
    "src/selfheal": "resilience",
    "src/services": "resilience",
    "src/core": "data",        # synthetic container for top-level src/*.py
    "infra/mydude": "infra",
}

CONTAINER_DESC: Dict[str, str] = {
    "src/swarm": "Multi-provider LLM swarm: Cogitation entrypoint, WaveOrchestrator, governance, broker, policy.",
    "src/promptopt": "DSPy GEPA/MIPROv2 prompt evolution with governed promotion.",
    "src/fleet": "Bot/Team fleet that runs governed bots through the Cogitation entrypoint.",
    "src/memory": "Unified memory substrate over local (Cognee) and cloud (Mem0) adapters.",
    "src/vendors": "Vendored Cognee (graph KG) and Mem0 (cloud memory) implementations.",
    "src/providers": "Provider-agnostic registry, adapters, and secret sourcing.",
    "src/bridge": "Remote connectivity (SSH/Paramiko) bridge.",
    "src/coach": "Life-coach sub-stack: empathy, mood ingestion, voice/avatar delivery.",
    "src/finance": "Finance sub-stack: Plaid transactions + QuickBooks attribution.",
    "src/browser": "Browser automation sub-stack (Browserbase / Playwright backends).",
    "src/avatar": "Humanistic avatar/voice sub-stack (ElevenLabs / HeyGen).",
    "src/subscriptions": "Subscription discovery & two-phase cancellation.",
    "src/selfheal": "Circuit breakers and provider health monitoring.",
    "src/web": "FastAPI app, auth, Jinja routes (legacy) and the live /api JSON router.",
    "src/core": "Top-level data core: ORM models, DB engine, app entrypoint.",
    "infra/mydude": "Jurisdiction routing and execution-locus topology.",
}

# Frontend sub-containers (all on the interface layer). The whole React SPA used
# to be one container, so every node package read "used in 1 container". Breaking
# frontend/src into the directories the codebase actually uses makes the node
# dependency map as granular as the Python one — placements now point at *where*
# in the UI a package is imported. Each entry's scan dir is the path itself.
# "frontend/src" is the SPA shell: it is scanned NON-recursively so the top-level
# files (App.tsx, main.tsx) are attributed to it while its sub-dirs (pages,
# components, ...) are attributed to their own containers (no double counting).
# Only sub-containers that exist on disk and contain real source files are
# created (pillar #1: only real, scanned placements).
FRONTEND_CONTAINERS: Dict[str, str] = {
    "frontend/src/pages": "React route pages — the dashboard's individual screens.",
    "frontend/src/components": "Reusable React UI components shared across pages.",
    "frontend/src/lib": "Frontend libraries: API client, helpers, shared logic.",
    "frontend/src/hooks": "Custom React hooks.",
    "frontend/src/contexts": "React context providers (global SPA state).",
    "frontend/src/registry": "Component/registry definitions for the SPA.",
    "frontend/src": "SPA shell: App.tsx + main.tsx entrypoint and root wiring.",
}


# ---------------------------------------------------------------------------
# Python distribution <-> import-name mapping (for the AST import scan)
# ---------------------------------------------------------------------------

# import-token (top-level module) -> python distribution name
PY_IMPORT_TO_DIST: Dict[str, str] = {
    "anthropic": "anthropic",
    "cryptography": "cryptography",
    "dspy": "dspy",
    "fastapi": "fastapi",
    "google": "google-generativeai",
    "idna": "idna",
    "itsdangerous": "itsdangerous",
    "jinja2": "jinja2",
    "openai": "openai",
    "optuna": "optuna",
    "paramiko": "paramiko",
    "playwright": "playwright",
    "psycopg2": "psycopg2-binary",
    "pyasn1": "pyasn1",
    "multipart": "python-multipart",
    "telegram": "python-telegram-bot",
    "yaml": "pyyaml",
    "requests": "requests",
    "sqlalchemy": "sqlalchemy",
    "starlette": "starlette",
    "urllib3": "urllib3",
    "uvicorn": "uvicorn",
}


# ---------------------------------------------------------------------------
# Provider catalog (each VERIFIED present in the source tree before insert)
# ---------------------------------------------------------------------------

PROVIDERS: List[dict] = [
    {"slug": "openai", "name": "OpenAI", "kind": "llm", "is_external": True,
     "capability_summary": "Cloud LLM chat/completions.", "homepage": "https://platform.openai.com",
     "tokens": ["openai"], "caps": [("llm.chat", True, 0)],
     "secret": {"env_var": "OPENAI_API_KEY", "sourced_via": "vault"}},
    {"slug": "anthropic", "name": "Anthropic", "kind": "llm", "is_external": True,
     "capability_summary": "Cloud LLM chat (Claude).", "homepage": "https://www.anthropic.com",
     "tokens": ["anthropic"], "caps": [("llm.chat", False, 1)],
     "secret": {"env_var": "ANTHROPIC_API_KEY", "sourced_via": "vault"}},
    {"slug": "gemini", "name": "Google Gemini", "kind": "llm", "is_external": True,
     "capability_summary": "Cloud LLM chat (Gemini).", "homepage": "https://ai.google.dev",
     "tokens": ["google.generativeai", "generativeai"], "caps": [("llm.chat", False, 2)],
     "secret": {"env_var": "GEMINI_API_KEY", "sourced_via": "vault"}},
    {"slug": "grok", "name": "xAI Grok", "kind": "llm", "is_external": True,
     "capability_summary": "Cloud LLM chat (Grok, OpenAI-compatible).", "homepage": "https://x.ai",
     "tokens": ["grok", "x.ai", "xai"], "caps": [("llm.chat", False, 3)],
     "secret": {"env_var": "GROK_API_KEY", "sourced_via": "vault"}},
    {"slug": "ollama", "name": "Ollama", "kind": "local_llm", "is_external": False,
     "capability_summary": "Self-hosted full-weight LLM inference (local execution locus).",
     "homepage": "https://ollama.com",
     "tokens": ["ollama"], "caps": [("llm.local", True, 0), ("llm.chat", False, 4)],
     "secret": None},
    {"slug": "mlx", "name": "Apple MLX", "kind": "local_llm", "is_external": False,
     "capability_summary": "On-device LLM inference (Apple silicon).", "homepage": "https://github.com/ml-explore/mlx",
     "tokens": ["mlx"], "caps": [("llm.local", False, 1)], "secret": None},
    {"slug": "cognee", "name": "Cognee", "kind": "graph", "is_external": False,
     "capability_summary": "Local knowledge-graph substrate (Private-Mode safe).",
     "homepage": "https://www.cognee.ai",
     "tokens": ["cognee"], "caps": [("memory.graph", True, 0), ("memory.semantic", False, 1)],
     "secret": None},
    {"slug": "mem0", "name": "Mem0", "kind": "memory", "is_external": True,
     "capability_summary": "Cloud semantic memory store.", "homepage": "https://mem0.ai",
     "tokens": ["mem0"], "caps": [("memory.semantic", True, 0)],
     "secret": {"env_var": "MEM0_API_KEY", "sourced_via": "connector_proxy"}},
    {"slug": "plaid", "name": "Plaid", "kind": "finance", "is_external": True,
     "capability_summary": "Bank transaction ingestion.", "homepage": "https://plaid.com",
     "tokens": ["plaid"], "caps": [("finance.transactions", True, 0)],
     "secret": {"env_var": "PLAID_SECRET", "sourced_via": "connector_proxy"}},
    {"slug": "quickbooks", "name": "QuickBooks Online", "kind": "finance", "is_external": True,
     "capability_summary": "Accounting / attribution writeback.", "homepage": "https://developer.intuit.com",
     "tokens": ["quickbooks", "intuit"], "caps": [("finance.accounting", True, 0)],
     "secret": {"env_var": None, "vault_key": "quickbooks_oauth", "sourced_via": "connector_proxy"}},
    {"slug": "browserbase", "name": "Browserbase", "kind": "browser", "is_external": True,
     "capability_summary": "Cloud headless browser (production browsing path).",
     "homepage": "https://browserbase.com",
     "tokens": ["browserbase"], "caps": [("browser.automation", True, 0)],
     "secret": {"env_var": "BROWSERBASE_API_KEY", "sourced_via": "connector_proxy"}},
    {"slug": "playwright", "name": "Playwright", "kind": "browser", "is_external": False,
     "capability_summary": "Local browser automation backend.", "homepage": "https://playwright.dev",
     "tokens": ["playwright"], "caps": [("browser.automation", False, 1)], "secret": None},
    {"slug": "elevenlabs", "name": "ElevenLabs", "kind": "voice", "is_external": True,
     "capability_summary": "Real-time TTS / voice cloning.", "homepage": "https://elevenlabs.io",
     "tokens": ["elevenlabs"], "caps": [("voice.tts", True, 0)],
     "secret": {"env_var": "ELEVENLABS_API_KEY", "sourced_via": "connector_proxy"}},
    {"slug": "hume", "name": "Hume AI", "kind": "emotion", "is_external": True,
     "capability_summary": "Expression / emotion measurement (mood signals).",
     "homepage": "https://hume.ai",
     "tokens": ["hume"], "caps": [("emotion.measurement", True, 0)],
     "secret": {"env_var": "HUME_API_KEY", "sourced_via": "connector_proxy"}},
    {"slug": "heygen", "name": "HeyGen", "kind": "avatar", "is_external": True,
     "capability_summary": "Streaming avatar video.", "homepage": "https://heygen.com",
     "tokens": ["heygen"], "caps": [("avatar.video", True, 0)],
     "secret": {"env_var": "HEYGEN_API_KEY", "sourced_via": "connector_proxy"}},
    {"slug": "paramiko-ssh", "name": "Paramiko SSH", "kind": "ssh", "is_external": False,
     "capability_summary": "Governed remote SSH execution (allow-listed).",
     "homepage": "https://www.paramiko.org",
     "tokens": ["paramiko"], "caps": [("remote.ssh", True, 0)], "secret": None},
    {"slug": "dspy", "name": "DSPy", "kind": "optimizer", "is_external": False,
     "capability_summary": "Prompt program optimization (GEPA + MIPROv2).",
     "homepage": "https://dspy.ai",
     "tokens": ["dspy"], "caps": [("prompt.optimization", True, 0)], "secret": None},
]

CAPABILITIES: List[dict] = [
    {"slug": "llm.chat", "name": "LLM Chat/Completions", "interface_ref": "src/swarm/llm_multi.py:MultiProviderLLM",
     "description": "Governed multi-provider chat inference with consensus."},
    {"slug": "llm.local", "name": "Local LLM Inference", "interface_ref": "src/providers/local_registry.py",
     "description": "Self-hosted / on-device inference for restricted execution locus."},
    {"slug": "memory.semantic", "name": "Semantic Memory", "interface_ref": "src/memory/substrate.py:MemorySubstrate",
     "description": "Vector recall / persistence of claims."},
    {"slug": "memory.graph", "name": "Graph Memory", "interface_ref": "src/vendors/cognee/graph.py:KnowledgeGraph",
     "description": "Relational / multi-hop knowledge-graph memory."},
    {"slug": "prompt.optimization", "name": "Prompt Optimization", "interface_ref": "src/promptopt/service.py",
     "description": "Reflective prompt evolution under governance."},
    {"slug": "finance.transactions", "name": "Finance Transactions", "interface_ref": "src/finance/providers.py",
     "description": "Bank/transaction ingestion."},
    {"slug": "finance.accounting", "name": "Accounting Writeback", "interface_ref": "src/finance/writeback.py",
     "description": "Accounting attribution and writeback."},
    {"slug": "browser.automation", "name": "Browser Automation", "interface_ref": "src/browser/backends.py",
     "description": "Headless browsing / page automation."},
    {"slug": "voice.tts", "name": "Text-to-Speech", "interface_ref": "src/avatar/voice.py",
     "description": "Voice synthesis."},
    {"slug": "voice.stt", "name": "Speech-to-Text", "interface_ref": "src/coach/ingestion.py",
     "description": "Speech recognition / transcription."},
    {"slug": "avatar.video", "name": "Avatar Video", "interface_ref": "src/avatar/providers.py",
     "description": "Streaming avatar video generation."},
    {"slug": "emotion.measurement", "name": "Emotion Measurement", "interface_ref": "src/coach/client_hume.py",
     "description": "Mood / expression measurement."},
    {"slug": "remote.ssh", "name": "Remote SSH", "interface_ref": "src/bridge/ssh.py",
     "description": "Governed remote command execution."},
]


# ---------------------------------------------------------------------------
# Filesystem / AST helpers
# ---------------------------------------------------------------------------

def _py_files(container_path: str, core_top_level: bool = False) -> List[str]:
    """Return .py files for a container. For the synthetic 'core' container,
    only top-level src/*.py files (not subpackages)."""
    abs_dir = os.path.join(ROOT, container_path) if not core_top_level else os.path.join(ROOT, "src")
    out: List[str] = []
    if core_top_level:
        for fn in os.listdir(abs_dir):
            if fn.endswith(".py") and os.path.isfile(os.path.join(abs_dir, fn)):
                out.append(os.path.join(abs_dir, fn))
        return out
    if not os.path.isdir(abs_dir):
        return out
    for dirpath, dirnames, filenames in os.walk(abs_dir):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if fn.endswith(".py"):
                out.append(os.path.join(dirpath, fn))
    return out


def _scan_functions(py_path: str) -> List[Tuple[str, str, str]]:
    """Return [(name, kind, signature)] of top-level defs/classes via ast."""
    try:
        src = open(py_path, "r", encoding="utf-8").read()
        tree = ast.parse(src)
    except Exception:
        return []
    res: List[Tuple[str, str, str]] = []
    entrypoint_names = {"run", "think", "think_sync", "main", "execute", "request", "handle"}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            res.append((node.name, "class", f"class {node.name}"))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            is_async = isinstance(node, ast.AsyncFunctionDef)
            args = [a.arg for a in node.args.args]
            sig = f"{'async ' if is_async else ''}def {node.name}({', '.join(args)})"
            if node.name in entrypoint_names:
                kind = "entrypoint"
            elif is_async:
                kind = "async_function"
            else:
                kind = "function"
            res.append((node.name, kind, sig[:500]))
    return res


def _scan_imports(py_path: str) -> Set[str]:
    """Return the set of top-level + dotted import tokens in a file."""
    try:
        src = open(py_path, "r", encoding="utf-8").read()
        tree = ast.parse(src)
    except Exception:
        return set()
    tokens: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                tokens.add(alias.name.split(".")[0])
                tokens.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                tokens.add(node.module.split(".")[0])
                tokens.add(node.module)
    return tokens


# Module-specifier patterns for ES/TS source (regex — Python has no TS parser).
_JS_FROM_RE = re.compile(r"""\bfrom\s*['"]([^'"]+)['"]""")
_JS_SIDEEFFECT_RE = re.compile(r"""\bimport\s*['"]([^'"]+)['"]""")
_JS_DYNAMIC_RE = re.compile(r"""\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)""")
_JS_REQUIRE_RE = re.compile(r"""\brequire\s*\(\s*['"]([^'"]+)['"]\s*\)""")


def _js_files(abs_dir: str, recurse: bool = True) -> List[str]:
    """Return .ts/.tsx/.js/.jsx files under abs_dir, skipping node_modules.

    When recurse is False, only files directly inside abs_dir are returned (used
    for the SPA-shell container so its sub-dirs are not double-counted)."""
    out: List[str] = []
    if not os.path.isdir(abs_dir):
        return out
    if not recurse:
        for fn in sorted(os.listdir(abs_dir)):
            full = os.path.join(abs_dir, fn)
            if os.path.isfile(full) and fn.endswith((".ts", ".tsx", ".js", ".jsx")):
                out.append(full)
        return out
    for dirpath, dirnames, filenames in os.walk(abs_dir):
        dirnames[:] = [d for d in dirnames if d != "node_modules"]
        for fn in filenames:
            if fn.endswith((".ts", ".tsx", ".js", ".jsx")):
                out.append(os.path.join(dirpath, fn))
    return out


def _pkg_from_specifier(spec: str) -> Optional[str]:
    """Resolve an import specifier to its npm package name, or None for
    relative ('./x'), absolute ('/x'), or local-alias ('@/x') imports."""
    if not spec or spec.startswith((".", "/")) or spec.startswith("@/"):
        return None
    parts = spec.split("/")
    if spec.startswith("@"):
        if len(parts) >= 2 and parts[0] and parts[1]:
            return f"{parts[0]}/{parts[1]}"
        return None
    return parts[0] or None


def _scan_js_imports(js_path: str) -> Set[str]:
    """Return the set of npm package names imported by a JS/TS file."""
    try:
        src = open(js_path, "r", encoding="utf-8", errors="ignore").read()
    except Exception:
        return set()
    specs: Set[str] = set()
    for rx in (_JS_FROM_RE, _JS_SIDEEFFECT_RE, _JS_DYNAMIC_RE, _JS_REQUIRE_RE):
        for m in rx.finditer(src):
            specs.add(m.group(1))
    pkgs: Set[str] = set()
    for spec in specs:
        name = _pkg_from_specifier(spec)
        if name:
            pkgs.add(name)
    return pkgs


def _scan_text(py_files: List[str]) -> str:
    chunks = []
    for p in py_files:
        try:
            chunks.append(open(p, "r", encoding="utf-8", errors="ignore").read().lower())
        except Exception:
            pass
    return "\n".join(chunks)


# ---------------------------------------------------------------------------
# Manifest parsers
# ---------------------------------------------------------------------------

def _parse_pyproject() -> List[Tuple[str, str]]:
    """Return [(name, version_spec)] of python direct deps from pyproject.toml."""
    path = os.path.join(ROOT, "pyproject.toml")
    out: List[Tuple[str, str]] = []
    if not os.path.isfile(path):
        return out
    txt = open(path, "r", encoding="utf-8").read()
    m = re.search(r"dependencies\s*=\s*\[(.*?)\]", txt, re.S)
    if not m:
        return out
    for line in m.group(1).splitlines():
        line = line.strip().strip(",").strip().strip('"').strip("'")
        if not line:
            continue
        dm = re.match(r"^([A-Za-z0-9_.\-]+)\s*(.*)$", line)
        if dm:
            out.append((dm.group(1).lower(), dm.group(2).strip() or None))
    return out


def _parse_frontend() -> List[Tuple[str, str, bool]]:
    """Return [(name, version_spec, is_dev)] from frontend/package.json."""
    path = os.path.join(ROOT, "frontend", "package.json")
    out: List[Tuple[str, str, bool]] = []
    if not os.path.isfile(path):
        return out
    data = json.load(open(path, "r", encoding="utf-8"))
    for name, ver in (data.get("dependencies") or {}).items():
        out.append((name, ver, False))
    for name, ver in (data.get("devDependencies") or {}).items():
        out.append((name, ver, True))
    return out


# ---------------------------------------------------------------------------
# Main seed
# ---------------------------------------------------------------------------

def seed() -> dict:
    # Rebuild everything EXCEPT the append-only audit log, so the rebuild history
    # accumulates across merges instead of being wiped on every reseed.
    init_ledger(drop=True, preserve=[LedgerEvent.__tablename__])
    db = SessionLocal()
    stats = {"layers": 0, "containers": 0, "functions": 0, "packages": 0,
             "providers": 0, "capabilities": 0, "placements": 0, "deps": 0}
    try:
        # 1. Layers
        layer_by_slug: Dict[str, Layer] = {}
        for ld in LAYERS:
            row = Layer(**ld)
            db.add(row)
            layer_by_slug[ld["slug"]] = row
            stats["layers"] += 1
        db.flush()

        # 2. Capabilities
        cap_by_slug: Dict[str, Capability] = {}
        for cd in CAPABILITIES:
            row = Capability(**cd)
            db.add(row)
            cap_by_slug[cd["slug"]] = row
            stats["capabilities"] += 1
        db.flush()

        # 3. Containers + functions (scanned from the real filesystem)
        container_by_path: Dict[str, Container] = {}
        container_imports: Dict[str, Set[str]] = {}
        container_node_imports: Dict[str, Set[str]] = {}
        container_text: Dict[str, str] = {}
        for cpath, lslug in CONTAINER_LAYER.items():
            is_core = cpath == "src/core"
            disk = os.path.join(ROOT, cpath if not is_core else "src")
            if not is_core and not os.path.isdir(disk):
                continue
            slug = cpath.replace("/", ".")
            cont = Container(
                layer_id=layer_by_slug[lslug].id,
                slug=slug,
                name=cpath.split("/")[-1],
                fs_path=cpath,
                language="python",
                description=CONTAINER_DESC.get(cpath),
            )
            db.add(cont)
            db.flush()
            container_by_path[cpath] = cont
            stats["containers"] += 1

            pyfiles = _py_files(cpath, core_top_level=is_core)
            imports: Set[str] = set()
            for pf in pyfiles:
                rel = os.path.relpath(pf, ROOT)
                for (name, kind, sig) in _scan_functions(pf):
                    db.add(Function(
                        container_id=cont.id, name=name, qualname=f"{rel}:{name}",
                        signature=sig, kind=kind,
                    ))
                    stats["functions"] += 1
                imports |= _scan_imports(pf)
            container_imports[cpath] = imports
            container_text[cpath] = _scan_text(pyfiles)

        # 3b. Frontend sub-containers (TypeScript/React) — granular interface map.
        #     Each sub-dir under frontend/src becomes its own container so node
        #     package placements point at *where* in the UI they are imported.
        for cpath, desc in FRONTEND_CONTAINERS.items():
            abs_dir = os.path.join(ROOT, cpath)
            # The SPA shell ("frontend/src") scans only its top-level files so its
            # sub-dirs are attributed to their own containers (no double-counting).
            recurse = cpath != "frontend/src"
            jsfiles = _js_files(abs_dir, recurse=recurse)
            if not jsfiles:
                continue  # skip empty / absent sub-containers (pillar #1: real only)
            name = "frontend" if cpath == "frontend/src" else cpath.split("/")[-1]
            cont = Container(
                layer_id=layer_by_slug["interface"].id,
                slug=cpath.replace("/", "."),
                name=name,
                fs_path=cpath,
                language="typescript",
                description=desc,
            )
            db.add(cont)
            db.flush()
            container_by_path[cpath] = cont
            stats["containers"] += 1
            node_imports: Set[str] = set()
            for jf in jsfiles:
                node_imports |= _scan_js_imports(jf)
            container_node_imports[cpath] = node_imports
        db.flush()

        # 4. Packages (python + node)
        pkg_by_key: Dict[Tuple[str, str], Package] = {}
        for (name, spec) in _parse_pyproject():
            row = Package(name=name, ecosystem="python", version_spec=spec,
                          is_direct=True, required=True, status="active")
            db.add(row)
            pkg_by_key[(name, "python")] = row
            stats["packages"] += 1
        for (name, spec, is_dev) in _parse_frontend():
            row = Package(name=name, ecosystem="node", version_spec=spec,
                          is_direct=True, is_dev=is_dev, required=not is_dev, status="active")
            db.add(row)
            pkg_by_key[(name, "node")] = row
            stats["packages"] += 1
        db.flush()

        # 5. Providers + capabilities + secrets (verified against source)
        prov_by_slug: Dict[str, Provider] = {}
        all_src_text = "\n".join(container_text.values())
        for pd in PROVIDERS:
            present = any(tok.lower() in all_src_text for tok in pd.get("tokens", []))
            row = Provider(
                slug=pd["slug"], name=pd["name"], kind=pd["kind"],
                capability_summary=pd.get("capability_summary"),
                homepage=pd.get("homepage"), is_external=pd.get("is_external", True),
                status="active" if present else "planned",
                notes=None if present else "Catalogued; no source reference found yet.",
            )
            db.add(row)
            db.flush()
            prov_by_slug[pd["slug"]] = row
            stats["providers"] += 1
            for (cap_slug, is_primary, tier) in pd.get("caps", []):
                cap = cap_by_slug.get(cap_slug)
                if cap:
                    db.add(ProviderCapability(
                        provider_id=row.id, capability_id=cap.id,
                        is_primary=is_primary, fallback_tier=tier,
                    ))
            sec = pd.get("secret")
            if sec:
                db.add(SecretRequirement(
                    provider_id=row.id,
                    env_var=sec.get("env_var"),
                    vault_key=sec.get("vault_key"),
                    required=sec.get("required", True),
                    sourced_via=sec.get("sourced_via", "connector_proxy"),
                    description="Sourced at runtime via connector proxy first, then vault/env (pillar #3).",
                ))
        db.flush()

        # 6. Placements: packages -> containers (real ast import scan)
        dist_to_pkg = {k: v for (k, v) in
                       [(p.name, p) for p in pkg_by_key.values() if p.ecosystem == "python"]}
        for cpath, imports in container_imports.items():
            cont = container_by_path[cpath]
            dists_used: Set[str] = set()
            for tok in imports:
                top = tok.split(".")[0]
                dist = PY_IMPORT_TO_DIST.get(top) or PY_IMPORT_TO_DIST.get(tok)
                if dist:
                    dists_used.add(dist)
            for dist in sorted(dists_used):
                pkg = dist_to_pkg.get(dist)
                if not pkg:
                    continue
                db.add(Placement(
                    subject_kind="package", subject_id=pkg.id,
                    layer_id=cont.layer_id, container_id=cont.id,
                    role="import dependency", criticality="normal",
                    evidence=f"ast-import-scan: {cpath}",
                ))
                stats["placements"] += 1
                db.add(ComponentDependency(
                    from_kind="container", from_id=cont.id,
                    to_kind="package", to_id=pkg.id, relation="imports",
                ))
                stats["deps"] += 1

        # 6b. Placements: node packages -> containers (real JS/TS import scan)
        name_to_node_pkg = {p.name: p for p in pkg_by_key.values()
                            if p.ecosystem == "node"}
        for cpath, node_pkgs in container_node_imports.items():
            cont = container_by_path[cpath]
            for name in sorted(node_pkgs):
                pkg = name_to_node_pkg.get(name)
                if not pkg:
                    continue
                db.add(Placement(
                    subject_kind="package", subject_id=pkg.id,
                    layer_id=cont.layer_id, container_id=cont.id,
                    role="import dependency", criticality="normal",
                    evidence=f"js-import-scan: {cpath}",
                ))
                stats["placements"] += 1
                db.add(ComponentDependency(
                    from_kind="container", from_id=cont.id,
                    to_kind="package", to_id=pkg.id, relation="imports",
                ))
                stats["deps"] += 1

        # 7. Placements: providers -> containers (token presence per container)
        for pd in PROVIDERS:
            prov = prov_by_slug[pd["slug"]]
            tokens = [t.lower() for t in pd.get("tokens", [])]
            for cpath, text in container_text.items():
                if any(tok in text for tok in tokens):
                    cont = container_by_path[cpath]
                    db.add(Placement(
                        subject_kind="provider", subject_id=prov.id,
                        layer_id=cont.layer_id, container_id=cont.id,
                        role=f"{prov.kind} provider",
                        criticality="high" if prov.kind in ("llm", "memory", "graph") else "normal",
                        evidence=f"token-scan: {cpath}",
                    ))
                    stats["placements"] += 1
                    db.add(ComponentDependency(
                        from_kind="container", from_id=cont.id,
                        to_kind="provider", to_id=prov.id, relation="wraps",
                    ))
                    stats["deps"] += 1

        # 8. Audit event — appended to the PRESERVED ledger_events table, so each
        #    rebuild adds one lasting row to an accumulating history (not a wipe).
        db.add(LedgerEvent(
            actor="seeder", action="seed", entity_kind="ledger",
            entity_ref="full-rebuild",
            summary="Rebuilt agent ledger from real project state.",
            payload_json=json.dumps(stats),
        ))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return stats


if __name__ == "__main__":
    s = seed()
    print("Agent ledger seeded from real project state:")
    for k, v in s.items():
        print(f"  {k:12} {v}")
