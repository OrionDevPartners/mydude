"""Structural AST signature extraction for capability routing.

Extracts structural signatures (function names, import lists, call patterns,
doc-strings) from Python source files using stdlib ``ast`` as the primary
parser. These signatures are richer than raw text for semantic indexing because
they isolate the *interface contract* of each capability handler from prose.

Optional tree-sitter augmentation is attempted for multi-language support
(JS/TS/C). If the ``tree_sitter`` package or required grammars are unavailable
the extractor degrades silently to Python-only — never raises.

Governance: no I/O gate required here (pure read-only AST scan); gate is
enforced in the DevGuard index layer that stores the signatures.
"""
from __future__ import annotations

import ast
import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_IGNORE_DIRS = frozenset({
    ".git", ".venv", "venv", "env", "__pycache__", "node_modules",
    ".local", ".cache", "dist", "build", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "extracted_capabilities", "vendor",
})

ZERO_TOKEN_THRESHOLD_DEFAULT = float(0.92)


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #

@dataclass
class CapabilitySignature:
    """Structural + semantic signature of a single capability."""
    name: str
    description: str
    required_fields: List[str] = field(default_factory=list)
    optional_fields: List[str] = field(default_factory=list)
    handler_imports: List[str] = field(default_factory=list)
    handler_calls: List[str] = field(default_factory=list)
    handler_file: str = ""
    handler_lineno: int = 0
    ast_hash: str = ""

    def structural_text(self) -> str:
        """Compact, structured representation used for embedding/TF-IDF."""
        parts: List[str] = [f"capability: {self.name}", f"description: {self.description}"]
        if self.required_fields:
            parts.append("required: " + ", ".join(self.required_fields))
        if self.optional_fields:
            parts.append("optional: " + ", ".join(self.optional_fields))
        if self.handler_imports:
            parts.append("imports: " + ", ".join(sorted(set(self.handler_imports))))
        if self.handler_calls:
            parts.append("calls: " + ", ".join(sorted(set(self.handler_calls))[:20]))
        return "\n".join(parts)


@dataclass
class BrokerHandlerInfo:
    """Info extracted from a broker capability dispatch branch."""
    capability: str
    lineno: int
    imports_used: List[str] = field(default_factory=list)
    calls_made: List[str] = field(default_factory=list)
    ast_hash: str = ""


# --------------------------------------------------------------------------- #
# stdlib ast helpers
# --------------------------------------------------------------------------- #

def _iter_python_files(root: Path) -> Iterator[Path]:
    for path in root.rglob("*.py"):
        if any(p in _IGNORE_DIRS for p in path.parts):
            continue
        yield path


def _extract_imports(tree: ast.AST) -> List[str]:
    imports: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split(".")[0])
    return sorted(imports)


def _extract_calls(node: ast.AST) -> List[str]:
    calls: List[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Attribute):
                calls.append(func.attr)
            elif isinstance(func, ast.Name):
                calls.append(func.id)
    return calls


def _node_hash(node: ast.AST) -> str:
    try:
        return hashlib.md5(ast.dump(node, annotate_fields=False).encode()).hexdigest()[:12]
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
# Broker handler extraction
# --------------------------------------------------------------------------- #

def _find_capability_strings(node: ast.AST) -> List[Tuple[str, int]]:
    """Find all `capability == "xyz"` comparisons in a broker-style dispatch."""
    found: List[Tuple[str, int]] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Compare):
            continue
        if len(child.ops) != 1 or not isinstance(child.ops[0], ast.Eq):
            continue
        left, right = child.left, child.comparators[0]
        name_node, str_node = None, None
        if isinstance(left, ast.Name) and isinstance(right, ast.Constant) and isinstance(right.value, str):
            name_node, str_node = left, right
        elif isinstance(right, ast.Name) and isinstance(left, ast.Constant) and isinstance(left.value, str):
            name_node, str_node = right, left
        if name_node and str_node and name_node.id == "capability":
            found.append((str_node.value, getattr(child, "lineno", 0)))
    return found


def _extract_integration_calls_in_branch(if_body: List[ast.stmt]) -> List[str]:
    """Extract self.integrations.METHOD() call names from an if-branch body."""
    calls: List[str] = []
    for stmt in if_body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Attribute)
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "self"
                and func.value.attr == "integrations"
            ):
                calls.append(func.attr)
    return calls


def _cap_name_from_test(test: ast.expr) -> Optional[str]:
    """Return the capability string from a `capability == "X"` comparison, or None."""
    if not isinstance(test, ast.Compare):
        return None
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return None
    left, right = test.left, test.comparators[0]
    if isinstance(left, ast.Name) and left.id == "capability" and isinstance(right, ast.Constant) and isinstance(right.value, str):
        return right.value
    if isinstance(right, ast.Name) and right.id == "capability" and isinstance(left, ast.Constant) and isinstance(left.value, str):
        return left.value
    return None


class _BrokerDispatchVisitor(ast.NodeVisitor):
    """Walk broker.py AST and map capability strings to their dispatch branches.

    Captures per-branch integration call lists so drift analysis can detect
    capabilities that call outbound integration methods without corresponding
    enforced contract preconditions.
    """

    def __init__(self, module_imports: List[str]) -> None:
        self._module_imports = module_imports
        self.handlers: Dict[str, BrokerHandlerInfo] = {}

    def visit_If(self, node: ast.If) -> None:
        cap_name = _cap_name_from_test(node.test)
        if cap_name:
            integration_calls = _extract_integration_calls_in_branch(node.body)
            h = BrokerHandlerInfo(
                capability=cap_name,
                lineno=node.lineno,
                imports_used=self._module_imports,
                calls_made=integration_calls,
                ast_hash=_node_hash(node),
            )
            self.handlers[cap_name] = h
        self.generic_visit(node)


def extract_broker_handlers(broker_path: Optional[Path] = None) -> Dict[str, BrokerHandlerInfo]:
    """Parse broker.py and return a map of capability_name -> BrokerHandlerInfo.

    Uses Python stdlib ast — no external dependency. Each BrokerHandlerInfo
    now includes the per-branch ``calls_made`` list (integration method names
    called within that handler branch), which powers the call-graph drift
    analysis in drift_detector.py.

    Falls back to an empty dict on any parse error (fail-soft, non-blocking
    for routing).
    """
    if broker_path is None:
        here = Path(__file__).resolve().parent
        broker_path = here / "broker.py"

    if not broker_path.exists():
        logger.debug("ast_router: broker.py not found at %s", broker_path)
        return {}

    try:
        source = broker_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except Exception as exc:
        logger.warning("ast_router: failed to parse broker.py: %s", exc)
        return {}

    module_imports = _extract_imports(tree)
    visitor = _BrokerDispatchVisitor(module_imports)
    visitor.visit(tree)
    return visitor.handlers


def index_into_devguard(*, force: bool = False) -> Dict[str, Any]:
    """Index capability structural signatures into the DevGuard dedup index.

    Converts each CapabilitySignature into a pseudo-Python function snippet
    and upserts it into the DevGuard DedupIndex so that structural duplicates
    (renamed copies of handler logic) are automatically surfaced.

    This is **alert-only** per the DevGuard design: it never edits source code,
    never blocks execution, and always fails-soft (exceptions are logged and an
    error dict is returned instead of propagating).

    Parameters
    ----------
    force : passed to DedupIndex to bypass the experimental-feature gate.

    Returns
    -------
    dict with keys ``indexed``, ``duplicates_found``, ``alerts``, ``error``.
    """
    try:
        from agentledger.experimental.devguard.index import DedupIndex
        from agentledger.experimental.devguard.extractor import (
            extract_units_from_source,
        )
    except ImportError as exc:
        logger.debug("ast_router.index_into_devguard: devguard unavailable (%s)", exc)
        return {"indexed": 0, "duplicates_found": 0, "alerts": [], "error": str(exc)}

    try:
        sigs = build_capability_signatures()
    except Exception as exc:
        logger.warning("ast_router.index_into_devguard: signature build failed: %s", exc)
        return {"indexed": 0, "duplicates_found": 0, "alerts": [], "error": str(exc)}

    try:
        idx = DedupIndex(force=force)
        idx.connect()
    except Exception as exc:
        logger.warning("ast_router.index_into_devguard: DedupIndex init failed: %s", exc)
        return {"indexed": 0, "duplicates_found": 0, "alerts": [], "error": str(exc)}

    indexed = 0
    all_alerts: List[Dict] = []
    errors: List[str] = []

    for cap_name, sig in sigs.items():
        calls_str = ", ".join(f'self.integrations.{c}(params)' for c in sig.handler_calls[:5]) or "pass"
        required_str = ", ".join(f"{f}: str" for f in sig.required_fields[:6]) or ""
        snippet = (
            f'def handle_{cap_name}(self, params):\n'
            f'    """{sig.description}"""\n'
            f'    # required: {required_str}\n'
            f'    {calls_str}\n'
        )
        try:
            alerts = idx.check(snippet, exclude_key=f"<caps>::handle_{cap_name}")
            indexed += 1
            for a in alerts:
                all_alerts.append({
                    "capability": cap_name,
                    "match_type": a.match_type,
                    "score": a.score,
                    "duplicate_of": a.qualname,
                    "file_path": a.file_path,
                })
        except Exception as exc:
            errors.append(f"{cap_name}: {exc}")

    try:
        idx.close()
    except Exception:
        pass

    return {
        "indexed": indexed,
        "duplicates_found": len(all_alerts),
        "alerts": all_alerts[:20],
        "error": "; ".join(errors) if errors else None,
    }


# --------------------------------------------------------------------------- #
# Multi-language optional tree-sitter augmentation
# --------------------------------------------------------------------------- #

def _try_treesitter_extract(path: Path) -> Optional[List[str]]:
    """Return a list of top-level function/method names via tree-sitter.

    Returns None if tree-sitter or the language grammar is unavailable (which
    is expected in most environments) — callers fall back to stdlib ast.

    Only JS/TS files are attempted here; Python files use stdlib ast directly.
    """
    suffix = path.suffix.lower()
    if suffix not in (".js", ".ts", ".tsx", ".jsx"):
        return None
    try:
        import tree_sitter  # type: ignore
        from tree_sitter_languages import get_language, get_parser  # type: ignore
        lang_name = "typescript" if suffix in (".ts", ".tsx") else "javascript"
        parser = get_parser(lang_name)
        source = path.read_bytes()
        ts_tree = parser.parse(source)
        names: List[str] = []
        # Walk the tree for function_declaration and method_definition nodes.
        cursor = ts_tree.walk()
        def _walk(node):
            if node.type in ("function_declaration", "method_definition", "arrow_function"):
                for child in node.children:
                    if child.type == "identifier":
                        names.append(child.text.decode("utf-8", errors="replace"))
            for child in node.children:
                _walk(child)
        _walk(ts_tree.root_node)
        return names
    except Exception:
        return None


def extract_js_function_names(root: Path) -> Dict[str, List[str]]:
    """Map JS/TS file paths to top-level function names (tree-sitter only).

    Returns an empty dict when tree-sitter is unavailable — callers continue
    with Python-only structural data.
    """
    out: Dict[str, List[str]] = {}
    for path in root.rglob("*.ts") if root.exists() else []:
        if any(p in _IGNORE_DIRS for p in path.parts):
            continue
        names = _try_treesitter_extract(path)
        if names is not None:
            out[str(path)] = names
    return out


# --------------------------------------------------------------------------- #
# Capability signature builder
# --------------------------------------------------------------------------- #

def build_capability_signatures(
    *,
    broker_path: Optional[Path] = None,
    include_js: bool = False,
    frontend_root: Optional[Path] = None,
) -> Dict[str, CapabilitySignature]:
    """Build structural signatures for every declared capability.

    Merges declared contract info (from capability_contracts.all_contracts())
    with AST-extracted handler info from broker.py. The result is a dict
    mapping capability_name → CapabilitySignature.

    Parameters
    ----------
    broker_path : optional path to broker.py (auto-located if None)
    include_js : if True, also attempt tree-sitter extraction of JS/TS files
    frontend_root : root dir to scan for JS/TS (used when include_js=True)
    """
    sigs: Dict[str, CapabilitySignature] = {}

    # 1) Seed from declared contracts (authoritative interface)
    try:
        from src.swarm.capability_contracts import all_contracts
        for contract in all_contracts():
            sigs[contract.capability] = CapabilitySignature(
                name=contract.capability,
                description=contract.description or contract.capability,
                required_fields=list(contract.required_fields),
                optional_fields=list(contract.optional_fields),
            )
    except Exception as exc:
        logger.warning("ast_router: failed to load capability contracts: %s", exc)

    # 2) Augment with broker handler AST info
    try:
        handlers = extract_broker_handlers(broker_path)
        for cap_name, info in handlers.items():
            if cap_name in sigs:
                sigs[cap_name].handler_imports = info.imports_used
                sigs[cap_name].handler_calls = info.calls_made
                sigs[cap_name].handler_file = str(info)
                sigs[cap_name].handler_lineno = info.lineno
                sigs[cap_name].ast_hash = info.ast_hash
            else:
                sigs[cap_name] = CapabilitySignature(
                    name=cap_name,
                    description=cap_name.replace("_", " "),
                    handler_imports=info.imports_used,
                    handler_calls=info.calls_made,
                    handler_lineno=info.lineno,
                    ast_hash=info.ast_hash,
                )
    except Exception as exc:
        logger.warning("ast_router: broker handler extraction failed: %s", exc)

    # 3) Optional JS/TS augmentation via tree-sitter
    if include_js and frontend_root:
        try:
            js_names = extract_js_function_names(frontend_root)
            logger.debug("ast_router: tree-sitter extracted %d JS/TS files", len(js_names))
        except Exception as exc:
            logger.debug("ast_router: JS/TS extraction failed (expected in most envs): %s", exc)

    logger.debug("ast_router: built %d capability signatures", len(sigs))
    return sigs


# --------------------------------------------------------------------------- #
# TF-IDF keyword scorer (no-embedding fallback)
# --------------------------------------------------------------------------- #

def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z][a-z0-9_]*", text.lower())


def tfidf_score(query_tokens: List[str], doc_tokens: List[str]) -> float:
    """Simple overlap-based similarity (zero-cost fallback for no embedding backend)."""
    if not query_tokens or not doc_tokens:
        return 0.0
    query_set = set(query_tokens)
    doc_set = set(doc_tokens)
    intersection = query_set & doc_set
    if not intersection:
        return 0.0
    return len(intersection) / (len(query_set) ** 0.5 * len(doc_set) ** 0.5)
