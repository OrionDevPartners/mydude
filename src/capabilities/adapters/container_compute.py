"""Container / local process compute capability adapter.

Wraps subprocess-based local execution behind the unified CapabilityAdapter
interface. This is the first real adapter for the "container_compute" category.

The capability contract mirrors what the existing SSH bridge and capability
broker enforce: commands must go through the allow-list defined in
``SSH_ALLOWED_COMMANDS`` and the ``CapabilityBroker`` + ``PolicyEngine`` before
execution. This adapter provides the execution primitive; the policy gate lives
in the broker (unchanged).

Availability: always available in the Replit container (subprocess module is
a Python built-in). The probe confirms that a benign command executes cleanly.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import Any, Dict, List, Optional

from src.capabilities.base import CapabilityAdapter, CapabilitySpec

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 30


class SubprocessComputeAdapter(CapabilityAdapter):
    """Local subprocess execution capability.

    Executes allow-listed commands in the local container. Commands are NOT
    gated here — that is the capability broker's job (PolicyEngine + allow-list
    enforcement). This adapter only provides the execution primitive and
    surfaces its availability.

    exec_locus=local: all execution is in the container, never outbound.
    """

    @property
    def exec_locus(self) -> str:
        return "local"

    def _probe(self) -> bool:
        """Confirm subprocess execution is functional with a benign probe."""
        try:
            result = subprocess.run(
                ["echo", "probe"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception as exc:
            logger.debug("container_compute probe failed: %s", exc)
            return False

    def health_probe(self) -> Dict[str, Any]:
        ok = self._probe()
        return {
            "ok": ok,
            "detail": "available (local subprocess, always in-container)" if ok
                      else "subprocess execution failed (unexpected — check container state)",
            "exec_locus": "local",
        }

    def run_command(
        self,
        command: List[str],
        *,
        timeout_s: int = _DEFAULT_TIMEOUT_S,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Execute ``command`` synchronously and return structured output.

        Returns ``{"ok": bool, "stdout": str, "stderr": str, "returncode": int}``.
        Raises ``ValueError`` on empty command. Does NOT enforce allow-lists —
        the capability broker is responsible for that gate before calling here.
        """
        if not command:
            raise ValueError("command must be a non-empty list")
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=cwd,
                env=env,
            )
            return {
                "ok": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "stdout": "",
                "stderr": "Command timed out after %d seconds." % timeout_s,
                "returncode": -1,
            }
        except Exception as exc:
            return {
                "ok": False,
                "stdout": "",
                "stderr": "Subprocess error: %s" % exc,
                "returncode": -1,
            }

    async def run_command_async(
        self,
        command: List[str],
        *,
        timeout_s: int = _DEFAULT_TIMEOUT_S,
        cwd: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Async variant — runs the blocking subprocess in a thread pool."""
        return await asyncio.to_thread(
            self.run_command, command,
            timeout_s=timeout_s, cwd=cwd,
        )
