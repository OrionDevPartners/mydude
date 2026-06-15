"""Modal burst backend adapter.

Provisions ephemeral Modal serverless functions for overflow inference.
Modal functions are invoked via the Modal REST API (no SDK install required
at runtime) using a deployment token sourced from the credential vault.

Credentials required (in vault / Replit Secrets):
  MODAL_TOKEN_ID      — Modal token ID
  MODAL_TOKEN_SECRET  — Modal token secret

If either secret is missing, is_configured() returns False and provision()
raises RuntimeError immediately (fail loud, pillar #1).

Modal functions are spawned as one-shot calls — each burst worker maps to a
single Modal function invocation. The function must expose an HTTP endpoint
(Modal web endpoint) that accepts the inference payload and returns the result.

The endpoint URL is configured via:
  MODAL_BURST_ENDPOINT_URL  — e.g. https://your-workspace--mydude-burst.modal.run
  (if unset, provision() raises — no silent fallback to a hardcoded URL)
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from typing import Any, Dict

from src.fleet.burst.interface import (
    BurstBackend,
    BurstWorkerHandle,
    BurstWorkerStatus,
    WorkerState,
)

logger = logging.getLogger(__name__)

_KEY = "modal"


def _get_secret(name: str) -> str:
    """Resolve a secret from connector proxy → vault → env (fail loud if absent)."""
    from src.providers.secrets import get_env
    val = get_env(name)
    if not val:
        raise RuntimeError(
            f"Burst backend '{_KEY}': required secret '{name}' is not set. "
            "Add it to the credential vault before using the Modal burst backend."
        )
    return val


class ModalBurstBackend(BurstBackend):
    """Modal serverless burst backend.

    Each provisioned worker corresponds to a live Modal function call routed
    through a pre-deployed Modal web endpoint.  The endpoint authenticates
    incoming requests with a shared bearer token (MODAL_TOKEN_SECRET).

    Adding a new backend: copy this file to e.g. ray_adapter.py, change ``_KEY``
    and the provisioning logic — the manager and API surface require no changes.
    """

    @property
    def key(self) -> str:
        return _KEY

    def is_configured(self) -> bool:
        from src.providers.secrets import get_env
        return bool(get_env("MODAL_TOKEN_ID")) and bool(get_env("MODAL_TOKEN_SECRET"))

    async def provision(
        self, worker_id: str, task: Dict[str, Any]
    ) -> BurstWorkerHandle:
        token_id = _get_secret("MODAL_TOKEN_ID")
        token_secret = _get_secret("MODAL_TOKEN_SECRET")
        from src.providers.secrets import get_env
        endpoint_url = get_env("MODAL_BURST_ENDPOINT_URL") or ""
        if not endpoint_url:
            raise RuntimeError(
                f"Burst backend '{_KEY}': MODAL_BURST_ENDPOINT_URL is not set. "
                "Deploy a Modal burst function and set this env var to its web endpoint URL."
            )
        if not endpoint_url.startswith("https://"):
            raise RuntimeError(
                f"Burst backend '{_KEY}': MODAL_BURST_ENDPOINT_URL must use https://. "
                f"Got: {endpoint_url[:80]}"
            )

        # ── Health probe: verify the endpoint is live before returning a handle ──
        # Modal serverless containers are pre-deployed; provision here means
        # confirming the deployment is reachable and accepting traffic before
        # the BurstManager registers the worker as ACTIVE.  This probe also
        # warms the cold-start container so the first dispatch is faster.
        health_body = json.dumps(
            {"health_check": True, "worker_id": worker_id}
        ).encode()
        health_req = urllib.request.Request(
            url=endpoint_url,
            data=health_body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token_secret}",
                "X-Burst-Health-Check": "1",
                "X-Burst-Worker-ID": worker_id,
            },
            method="POST",
        )

        def _probe() -> int:
            try:
                with urllib.request.urlopen(health_req, timeout=10) as resp:
                    return resp.status
            except urllib.error.HTTPError as e:
                return e.code
            except Exception as e:
                raise RuntimeError(
                    f"Modal burst endpoint unreachable during provision: {e}"
                ) from e

        try:
            status_code = await asyncio.to_thread(_probe)
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(
                f"Modal burst endpoint unreachable during provision: {e}"
            ) from e

        if status_code not in (200, 201, 202):
            raise RuntimeError(
                f"Burst backend '{_KEY}': endpoint health check returned HTTP {status_code}. "
                "Verify MODAL_BURST_ENDPOINT_URL points to a live Modal web endpoint "
                "and that MODAL_TOKEN_SECRET is correct."
            )

        backend_ref = f"{endpoint_url}?worker_id={worker_id}"
        handle = BurstWorkerHandle(
            worker_id=worker_id,
            backend_key=_KEY,
            backend_ref=backend_ref,
            metadata={
                "endpoint_url": endpoint_url,
                "goal_preview": str(task.get("goal", ""))[:200],
                "domain": task.get("domain", "general"),
            },
        )
        logger.info(
            "Modal burst: provisioned worker %s → %s (health probe HTTP %d)",
            worker_id, endpoint_url, status_code,
        )
        return handle

    async def dispatch(
        self, handle: BurstWorkerHandle, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        token_secret = _get_secret("MODAL_TOKEN_SECRET")
        endpoint_url = handle.metadata.get("endpoint_url", "")
        if not endpoint_url:
            raise RuntimeError(
                f"Modal burst: handle for worker {handle.worker_id} has no endpoint_url. "
                "This is a bug — BurstManager should only dispatch to provisioned handles."
            )

        body = json.dumps({
            "worker_id": handle.worker_id,
            **payload,
        }).encode()

        req = urllib.request.Request(
            url=endpoint_url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token_secret}",
                "X-Burst-Worker-ID": handle.worker_id,
            },
            method="POST",
        )

        def _call():
            timeout = payload.get("timeout_sec", 120)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            return json.loads(raw)

        try:
            result = await asyncio.to_thread(_call)
            logger.info("Modal burst: worker %s dispatch ok", handle.worker_id)
            if not isinstance(result, dict):
                result = {"ok": True, "text": str(result)}
            result.setdefault("ok", True)
            return result
        except urllib.error.HTTPError as e:
            body_text = ""
            try:
                body_text = e.read().decode(errors="replace")[:500]
            except Exception:
                pass
            raise RuntimeError(
                f"Modal burst dispatch failed for worker {handle.worker_id}: "
                f"HTTP {e.code} — {body_text}"
            ) from e
        except Exception as e:
            raise RuntimeError(
                f"Modal burst dispatch failed for worker {handle.worker_id}: {e}"
            ) from e

    async def status(self, handle: BurstWorkerHandle) -> BurstWorkerStatus:
        endpoint_url = handle.metadata.get("endpoint_url", "")
        if not endpoint_url:
            return BurstWorkerStatus(
                worker_id=handle.worker_id,
                state=WorkerState.FAILED,
                backend_key=_KEY,
                detail="no endpoint_url in handle metadata",
            )
        import socket
        from urllib.parse import urlparse
        try:
            parsed = urlparse(endpoint_url)
            host = parsed.hostname or ""
            port = parsed.port or 443
            sock = socket.create_connection((host, port), timeout=2.0)
            sock.close()
            return BurstWorkerStatus(
                worker_id=handle.worker_id,
                state=WorkerState.READY,
                backend_key=_KEY,
                detail=f"endpoint reachable: {host}:{port}",
            )
        except Exception as e:
            return BurstWorkerStatus(
                worker_id=handle.worker_id,
                state=WorkerState.FAILED,
                backend_key=_KEY,
                detail=f"endpoint unreachable: {e}",
            )

    async def teardown(self, handle: BurstWorkerHandle) -> None:
        """Release this burst worker slot with a best-effort drain notification.

        Modal serverless functions are stateless and auto-terminate after each
        call, so teardown here means notifying the endpoint that no more
        dispatches will follow (allows any graceful shutdown logic in the
        endpoint to run).  If the notification fails, it is silently dropped —
        Modal will reclaim the container regardless.
        """
        endpoint_url = handle.metadata.get("endpoint_url", "")
        if not endpoint_url:
            logger.debug(
                "Modal burst: teardown worker %s — no endpoint_url; nothing to notify",
                handle.worker_id,
            )
            return

        try:
            from src.providers.secrets import get_env
            token_secret = get_env("MODAL_TOKEN_SECRET") or ""
            if not token_secret:
                logger.debug(
                    "Modal burst: teardown worker %s — no token; skipping drain notification",
                    handle.worker_id,
                )
                return

            drain_body = json.dumps(
                {"teardown": True, "worker_id": handle.worker_id}
            ).encode()
            drain_req = urllib.request.Request(
                url=endpoint_url,
                data=drain_body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token_secret}",
                    "X-Burst-Teardown": "1",
                    "X-Burst-Worker-ID": handle.worker_id,
                },
                method="POST",
            )

            def _notify():
                try:
                    with urllib.request.urlopen(drain_req, timeout=5):
                        pass
                except Exception:
                    pass  # best-effort; Modal auto-reclaims after call completion

            await asyncio.to_thread(_notify)
        except Exception as e:
            logger.debug(
                "Modal burst: teardown drain notification failed for %s: %s",
                handle.worker_id, e,
            )
        logger.debug(
            "Modal burst: teardown worker %s complete (drain notification sent)",
            handle.worker_id,
        )
