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
- **Adding a NEW dependency:** `uv add` / `installLanguagePackages` trigger a full
  universal re-lock and hit the same wall regardless of what you add. Capping
  `requires-python` (`<3.14`, `<3.13`) just moves the failure to the next split
  and caused other conflicts — not worth it.
- **`installLanguagePackages` also mangles PEP 508 markers** (rewrote
  `lancedb; python_version < '3.13'` into a `====` version pin), so you can't add
  a marker-guarded dep through it.
- **Dev-only deps:** install with `uv pip install <pkg>` (per-interpreter, no
  universal lock, not added to the prod closure) and make them reproducible in dev
  via `scripts/post-merge.sh`. Note: `uv sync` makes the env exactly match the
  lock and will *uninstall* anything not in it — so any `uv pip install` line must
  run AFTER `uv sync` in the script.
