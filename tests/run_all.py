#!/usr/bin/env python3
"""Standalone test runner for the MyDude.io ``tests/`` suite.

This repository's test suites are written to be runnable *without* pytest:
each ``tests/test_*.py`` file exposes ``test_*`` functions and a
``if __name__ == "__main__":`` block that runs them all and exits non-zero on
the first failure.  pytest is intentionally **not** installed in this
environment, so this runner drives the suites directly via the interpreter.

Usage:
    python tests/run_all.py                # run every tests/test_*.py
    python tests/run_all.py security        # run files whose name contains "security"
    python tests/run_all.py security finance # run files matching any pattern

Each test file is executed in its own subprocess (full isolation: no shared
module state, limiter caches, or env leakage between suites).  The runner
exits 0 only if every selected suite exits 0, so it can be wired up as a
validation / CI check that blocks on any regression.

Environment knobs:
    TEST_TIMEOUT   per-file timeout in seconds (default 300)
    TEST_JOBS      max parallel suites (default: CPU count, capped at 8)
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent


def _discover(patterns: list[str]) -> list[Path]:
    files = sorted(p for p in TESTS_DIR.glob("test_*.py") if p.is_file())
    if not patterns:
        return files
    selected: list[Path] = []
    for f in files:
        if any(pat in f.name for pat in patterns):
            selected.append(f)
    return selected


def _run_one(path: Path, timeout: int) -> tuple[Path, int, float, str]:
    env = dict(os.environ)
    # Ensure suites can import the ``src`` package regardless of CWD.
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    start = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, str(path)],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        rc = proc.returncode
        out = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as e:
        rc = 124
        captured = ""
        if e.stdout:
            captured += e.stdout if isinstance(e.stdout, str) else e.stdout.decode("utf-8", "replace")
        if e.stderr:
            captured += e.stderr if isinstance(e.stderr, str) else e.stderr.decode("utf-8", "replace")
        out = captured + "\n[run_all] TIMEOUT after %ds\n" % timeout
    elapsed = time.monotonic() - start
    return path, rc, elapsed, out


def main(argv: list[str]) -> int:
    patterns = [a for a in argv if not a.startswith("-")]
    files = _discover(patterns)
    if not files:
        print("[run_all] no test files matched %r" % (patterns,))
        return 1

    timeout = int(os.environ.get("TEST_TIMEOUT", "300"))
    default_jobs = min(8, os.cpu_count() or 1)
    jobs = max(1, int(os.environ.get("TEST_JOBS", str(default_jobs))))

    print(
        "[run_all] running %d suite(s) with %d worker(s), %ds timeout each\n"
        % (len(files), jobs, timeout)
    )

    results: dict[Path, tuple[int, float, str]] = {}
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(_run_one, f, timeout): f for f in files}
        for fut in as_completed(futures):
            path, rc, elapsed, out = fut.result()
            results[path] = (rc, elapsed, out)
            status = "PASS" if rc == 0 else ("TIMEOUT" if rc == 124 else "FAIL")
            print("%-7s %-45s (%.1fs)" % (status, path.name, elapsed))

    failed = [p for p, (rc, _e, _o) in results.items() if rc != 0]
    total_elapsed = time.monotonic() - started

    if failed:
        print("\n" + "=" * 70)
        print("FAILED SUITES (%d):" % len(failed))
        print("=" * 70)
        for p in sorted(failed):
            rc, _e, out = results[p]
            print("\n----- %s (exit %d) -----" % (p.name, rc))
            tail = "\n".join(out.rstrip().splitlines()[-25:])
            print(tail)

    print("\n" + "=" * 70)
    print(
        "%d/%d suite(s) passed in %.1fs"
        % (len(files) - len(failed), len(files), total_elapsed)
    )
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
