"""DevGuard CLI — alert-only duplicate scanner.

Usage:
    python -m agentledger.experimental.devguard.cli check <file> [<file> ...]
    python -m agentledger.experimental.devguard.cli scan [<root> ...]
    python -m agentledger.experimental.devguard.cli watch [<root> ...]
    python -m agentledger.experimental.devguard.cli index [--force] [<root> ...]
    python -m agentledger.experimental.devguard.cli stats

Options (check/scan):
    --threshold <float>   semantic similarity cutoff (default 0.85)
    --force               bypass the production gate (dev override)

Dev-gated: blocked when REPLIT_DEPLOYMENT=1 unless AGENT_MEMORY_STACK=1 or
--force. Alert-only: nothing here ever edits, merges, or synthesizes code.
Exit code is 1 when duplicates are found (so it can act as a CI/pre-build gate),
0 when clean.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Sequence

from .alerts import default_sink
from .scanner import check_file, get_index, index_codebase


def _pop_flag(args: list[str], name: str) -> bool:
    if name in args:
        args.remove(name)
        return True
    return False


def _pop_opt_float(args: list[str], name: str, default: float) -> float:
    if name in args:
        i = args.index(name)
        try:
            value = float(args[i + 1])
        except (IndexError, ValueError):
            raise SystemExit(f"{name} requires a numeric argument")
        del args[i : i + 2]
        return value
    return default


def _cmd_check(args: list[str]) -> int:
    force = _pop_flag(args, "--force")
    threshold = _pop_opt_float(args, "--threshold", 0.85)
    if not args:
        print("usage: cli check <file> [<file> ...]", file=sys.stderr)
        return 2
    sink = default_sink()
    total = 0
    errors = 0
    for f in args:
        p = Path(f)
        if not p.exists():
            print(f"not found: {f}", file=sys.stderr)
            errors += 1
            continue
        try:
            results = check_file(p, threshold=threshold, force=force)
        except (SyntaxError, UnicodeDecodeError) as exc:
            print(f"[ERROR] cannot parse {p}: {exc}", file=sys.stderr)
            errors += 1
            continue
        if not results:
            sink.emit([], source=str(p))
            continue
        for qualname, alerts in results.items():
            total += len(alerts)
            sink.emit(alerts, source=f"{p}::{qualname}")
    if errors:
        return 2
    return 1 if total else 0


def _resolve_roots(roots: Sequence[str]) -> list[Path]:
    from .extractor import iter_python_files
    from .index import _default_roots

    sources = [Path(r) for r in roots] if roots else _default_roots()
    paths: list[Path] = []
    for root in sources:
        if root.exists():
            paths.extend(iter_python_files(root))
    return paths


def _cmd_scan(args: list[str]) -> int:
    force = _pop_flag(args, "--force")
    threshold = _pop_opt_float(args, "--threshold", 0.85)
    paths = _resolve_roots(args)
    sink = default_sink()
    total = 0
    for p in paths:
        results = check_file(p, threshold=threshold, force=force)
        for qualname, alerts in results.items():
            total += len(alerts)
            sink.emit(alerts, source=f"{p}::{qualname}")
    print(f"\nscanned {len(paths)} file(s); {total} duplicate alert(s)")
    return 1 if total else 0


def _cmd_index(args: list[str]) -> int:
    force = _pop_flag(args, "--force")
    roots = args or None
    stats = index_codebase(roots=roots, force=force)
    print(
        f"indexed {stats['units']} units from {stats['files']} file(s) "
        f"({stats['errors']} skipped)"
    )
    return 0


def _cmd_stats(args: list[str]) -> int:
    force = _pop_flag(args, "--force")
    idx = get_index(force=force)
    print(f"db:            {idx.db_path}")
    print(f"embedder:      {idx.embedder_id} (dim {idx.dim})")
    print(f"indexed units: {idx.count()}")
    return 0


def _cmd_watch(args: list[str]) -> int:
    force = _pop_flag(args, "--force")
    threshold = _pop_opt_float(args, "--threshold", 0.85)
    from .watcher import watch

    return watch(args or None, threshold=threshold, force=force)


_COMMANDS = {
    "check": _cmd_check,
    "scan": _cmd_scan,
    "watch": _cmd_watch,
    "index": _cmd_index,
    "stats": _cmd_stats,
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = args[0], args[1:]
    handler = _COMMANDS.get(cmd)
    if handler is None:
        print(f"unknown command: {cmd}\n", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        return 2
    return handler(rest)


if __name__ == "__main__":
    raise SystemExit(main())
