#!/bin/sh
# Container entrypoint: wait for the database to accept TCP connections before
# launching the app, then exec the given command. This avoids a first-boot race
# where the app starts before the DB is ready (common on container platforms
# such as Azure Container Apps, Cloud Run, Fly.io, docker compose, etc).
set -e

python - <<'PY'
import os, time, socket
from urllib.parse import urlparse

url = os.environ.get("DATABASE_URL", "")
if url:
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=3):
                print(f"[entrypoint] database {host}:{port} is reachable", flush=True)
                break
        except OSError as exc:
            print(f"[entrypoint] waiting for database {host}:{port} ... ({exc})", flush=True)
            time.sleep(2)
    else:
        print("[entrypoint] WARNING: database not reachable before timeout", flush=True)
PY

exec "$@"
