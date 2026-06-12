---
name: uv frozen sync / adding deps
description: Why plain `uv sync` and `uv add` fail in this repl, and how to add packages anyway.
---

# uv re-resolution fails on the Python 3.14 split

**Symptom:** `uv sync` and `installLanguagePackages`/`uv add` fail with
"No solution found ... split (python_full_version >= '3.14' ...): only
optuna<4.9.0 is available and your project depends on optuna>=4.9.0".

**Root cause:** `requires-python = ">=3.11"` is unbounded, so uv's *universal*
resolver must satisfy every Python up to 3.14. optuna 4.9.0 itself is pure-Python
(`>=3.9`), but a *transitive* dep of it has no 3.14 (and 3.13) wheel, so the
universal resolve is unsatisfiable. The active interpreter is 3.11; the app runs
fine — this only bites when uv re-resolves.

**Rules:**
- **Installing locked deps:** use `uv sync --frozen` (installs from the known-good
  `uv.lock` without re-resolving). Plain `uv sync` re-resolves and fails. The
  post-merge script (`scripts/post-merge.sh`) uses `--frozen` for this reason.
- **Adding a NEW dependency — NOW WORKS:** `requires-python` is bounded to
  `">=3.11,<3.13"` in `pyproject.toml` (and the vestigial pytorch-cpu `optuna`/
  `huggingface-hub` source mappings were removed), so the optuna 3.13/3.14 split
  is gone. A plain `uv add <pkg>` re-locks cleanly and commits to both
  `pyproject.toml` + `uv.lock` (verified: `uv add boto3` resolved 116 pkgs fine).
  This supersedes the old "re-lock always fails" rule below. Still back up
  `pyproject.toml`/`uv.lock` before and sanity-check the diff, but the universal
  re-resolve no longer hits a wall for normal PyPI deps. (A dep whose own closure
  reintroduces a 3.13+-only transitive could still split — handle case by case.)
- **`installLanguagePackages` also mangles PEP 508 markers** (rewrote
  `lancedb; python_version < '3.13'` into a `====` version pin), so you can't add
  a marker-guarded dep through it.
- **Dev-only deps:** install with `uv pip install <pkg>` (per-interpreter, no
  universal lock, not added to the prod closure) and make them reproducible in dev
  via `scripts/post-merge.sh`. Note: `uv sync` makes the env exactly match the
  lock and will *uninstall* anything not in it — so any `uv pip install` line must
  run AFTER `uv sync` in the script.
