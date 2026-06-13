---
name: Docker inside the Replit container
description: Quirks of running Docker/compose for testing inside this Replit workspace container.
---

The Docker daemon **is** reachable inside this workspace (build + run work), but there are nested-container limitations:

- **`docker exec` / `docker compose exec` fails** with `OCI runtime exec failed ... setns process ... exit status 1`. This breaks anything that shells into a running container — including Compose `healthcheck:` tests (they run via exec) and `psql`/inspection via `exec`.
  - **How to apply:** Don't gate `depends_on` on `condition: service_healthy` — the health probe can never pass, so dependents never start. Use plain `depends_on: [db]` (service_started) plus an entrypoint that waits for the DB TCP port before launching the app.
  - To verify DB/state, query over a **published port via HTTP/TCP from the host**, not `docker exec`.

- **Host port 5000 is occupied** by the Replit dev workflow (`python main.py`). Map containers to a different host port (e.g. `5050:5000`) or curls to `localhost:5000` will hit the workflow, not your container.

- The Replit preview pane only proxies workflow ports, so a container on `5050` is reachable from the shell but **not** visible in the preview pane.
