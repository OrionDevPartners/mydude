#!/bin/bash
set -e

# Sync Python dependencies from pyproject.toml / uv.lock (idempotent, non-interactive).
uv sync

# Note: database schema is auto-migrated on app startup via _sync_missing_columns,
# so no separate migration step is required here.
