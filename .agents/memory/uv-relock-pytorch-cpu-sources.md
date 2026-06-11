---
name: uv re-lock blocked by vestigial pytorch-cpu sources
description: Why `uv lock`/`uv sync` fails here on packages that clearly exist on PyPI, and how to unblock a dependency upgrade.
---

# uv re-lock blocked by vestigial `[tool.uv.sources]`

When upgrading any dependency in this repo, a targeted `uv lock --upgrade-package …`
(or plain `uv sync`) fails with errors like
`Because only <pkg>{...}<X.Y is available and your project depends on <pkg>>=X.Y …
requirements are unsatisfiable` — even though that version exists on PyPI.

**Cause:** `pyproject.toml` carries a huge auto-generated `[tool.uv.sources]` block
(~1100 packages) pinning everything to an `explicit` `pytorch-cpu` index, but the
committed `uv.lock` resolves the entire closure from PyPI (zero entries actually use
`download.pytorch.org`). The sources block is inconsistent with the lock; the
restricted index lacks the required versions, so re-resolution can't satisfy them.

**Why it matters / how to apply:**
- Only packages that are BOTH in `[tool.uv.sources]` AND in the resolved closure
  block the re-lock. Compute the blast radius before editing: intersect the locked
  package names (`name = "…"` in `uv.lock`) with the pytorch-cpu-mapped names. As of
  this writing that intersection was just `optuna` and `huggingface-hub`.
- Remove the mappings for those packages (and their inert family members like
  `optuna-dashboard`/`optuna-integration`). This only makes `pyproject` consistent
  with the lock's existing PyPI resolution — it does NOT change how anything resolves
  (nothing uses the pytorch-cpu index). Then re-lock + sync.
- Also bound `requires-python` (repo runs Python 3.11). Unbounded `>=3.11` hits an
  optuna 3.13/3.14 resolution split that fails separately.
- **Deploy carries the lock, not pyproject:** `scripts/post-merge.sh` installs with
  `uv sync --frozen`, so any dependency/security fix MUST land in `uv.lock` or it
  never reaches production.
