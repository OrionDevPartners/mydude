"""AST extraction + structural hashing for DevGuard (pure stdlib, no I/O gate).

Ports the *real* structural-skeleton hash from the vendored
``ast-semantic-consolidator`` (its scan path used a degenerate
``md5(type(node).__name__)`` "simplified for speed"; the operative algorithm is
:class:`SkeletonVisitor` + :func:`normalized_hash`).

Two hashes per code unit:

* ``normalized_hash`` — md5 of the ordered sequence of AST node *type names*.
  Identifiers, argument names, and literal values are never emitted, so two
  functions that are identical except for their names collide. This is the
  exact-logic / "renamed copy" detector.
* ``exact_hash`` — md5 of ``ast.dump`` (identifiers included): byte-for-byte
  identical ASTs.

Everything here is a pure function over source text — no DB, no network, no
credentials — so it carries no production gate (the gate guards store
initialization in :mod:`devguard.index`).
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

# Directory names never indexed.
DEFAULT_IGNORE_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        "node_modules",
        "extracted_capabilities",
        ".local",
        ".cache",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)

# Path fragments skipped entirely. The vendored repos are read-only reference
# code and must NOT pollute MyDude's own dedup index.
DEFAULT_IGNORE_PARTS = frozenset({"vendor"})


@dataclass(frozen=True)
class CodeUnit:
    """A single indexable function or class extracted from source."""

    name: str
    qualname: str
    node_type: str  # "Function" | "Class"
    file_path: str
    lineno: int
    end_lineno: int
    loc: int
    source: str
    normalized_hash: str
    exact_hash: str
    imports: tuple[str, ...]

    @property
    def key(self) -> str:
        """Stable join key: ``<file_path>::<qualname>``."""
        return f"{self.file_path}::{self.qualname}"


class SkeletonVisitor(ast.NodeVisitor):
    """Collect AST node *type names* in traversal order (identifiers stripped)."""

    def __init__(self) -> None:
        self.skeleton: list[str] = []

    def generic_visit(self, node: ast.AST) -> None:
        self.skeleton.append(type(node).__name__)
        super().generic_visit(node)


def normalized_hash(node: ast.AST) -> str:
    """Structural skeleton hash — invariant to identifier/literal renaming."""
    visitor = SkeletonVisitor()
    visitor.visit(node)
    return hashlib.md5(",".join(visitor.skeleton).encode("utf-8")).hexdigest()


def exact_hash(node: ast.AST) -> str:
    """Byte-for-byte AST hash (identifiers included)."""
    return hashlib.md5(
        ast.dump(node, annotate_fields=False).encode("utf-8")
    ).hexdigest()


def file_content_hash(source: str) -> str:
    """Content hash of a whole file (incremental-scan skip key)."""
    return hashlib.md5(source.encode("utf-8")).hexdigest()


def _module_imports(tree: ast.AST) -> tuple[str, ...]:
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    return tuple(sorted(imports))


class _UnitExtractor(ast.NodeVisitor):
    def __init__(
        self, source_lines: list[str], file_path: str, imports: tuple[str, ...]
    ) -> None:
        self.source_lines = source_lines
        self.file_path = file_path
        self.imports = imports
        self.units: list[CodeUnit] = []
        self._scope: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._capture(node, "Function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._capture(node, "Function")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._capture(node, "Class")

    def _capture(self, node: ast.AST, node_type: str) -> None:
        name = getattr(node, "name", "<anon>")
        qualname = ".".join(self._scope + [name])
        end = getattr(node, "end_lineno", None) or node.lineno
        src = "".join(self.source_lines[node.lineno - 1 : end])
        self.units.append(
            CodeUnit(
                name=name,
                qualname=qualname,
                node_type=node_type,
                file_path=self.file_path,
                lineno=node.lineno,
                end_lineno=end,
                loc=len(src.splitlines()),
                source=src,
                normalized_hash=normalized_hash(node),
                exact_hash=exact_hash(node),
                imports=self.imports,
            )
        )
        # Recurse into the body so nested defs/methods are captured with scope.
        self._scope.append(name)
        self.generic_visit(node)
        self._scope.pop()


def extract_units_from_source(source: str, file_path: str) -> list[CodeUnit]:
    """Parse ``source`` and return every function/class as a :class:`CodeUnit`.

    Raises :class:`SyntaxError` on unparseable input (fail loud — callers decide
    whether to skip-and-log a single bad file).
    """
    tree = ast.parse(source)
    source_lines = source.splitlines(keepends=True)
    imports = _module_imports(tree)
    extractor = _UnitExtractor(source_lines, file_path, imports)
    extractor.visit(tree)
    return extractor.units


def extract_units_from_file(path: str | Path) -> list[CodeUnit]:
    path = Path(path)
    source = path.read_text(encoding="utf-8")
    return extract_units_from_source(source, str(path))


def iter_python_files(
    root: str | Path,
    *,
    ignore_dirs: Iterable[str] = DEFAULT_IGNORE_DIRS,
    ignore_parts: Iterable[str] = DEFAULT_IGNORE_PARTS,
) -> Iterator[Path]:
    """Yield ``*.py`` files under ``root`` excluding ignored dirs/path parts."""
    root = Path(root)
    ignore_dirs = frozenset(ignore_dirs)
    ignore_parts = frozenset(ignore_parts)
    for path in root.rglob("*.py"):
        parts = set(path.parts)
        if parts & ignore_dirs:
            continue
        if parts & ignore_parts:
            continue
        yield path
