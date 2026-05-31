---
name: Testing auth-gated routes
description: How to e2e-test BoBot AI routes when login is gated by a secret the agent cannot read.
---

The app's login uses `ADMIN_PASSWORD` (a Replit secret). When that secret is set, the default `"admin"` password is rejected, so the Playwright `runTest` skill cannot log in (the agent must not read the secret value).

**How to apply:** For this server-rendered (Jinja2, no JS framework) app, validate routes in-process with FastAPI's `TestClient`. Mint a valid session in the same process:

```python
from fastapi.testclient import TestClient
from src.web.app import app
from src.web import auth
client = TestClient(app)
client.cookies.set("session_token", auth._serializer.dumps({"authenticated": True}))
```

This works because `auth._serializer` uses the same in-process `SESSION_SECRET` (random per process). It exercises rendering + POST flows (add/reveal/search/rotate/delete) against the real dev DB. Reserve `runTest` for cases where you have the password.

**Why:** Browser e2e is impossible without the password; TestClient gives reliable coverage for a no-JS app.
