"""Secrets vault capability adapters.

Two real adapters for the "secrets_vault" category:

  * ``ConnectorProxyVaultAdapter`` — queries the Replit connector proxy
    (src.web.connectors) to resolve secrets from connected integrations.
    Available when the proxy hostname + identity token are present.

  * ``EnvVaultAdapter`` — reads secrets from the process environment (the
    existing src.providers.secrets layer). Always available as the local
    fallback (env_2 last resort in the connector-proxy → vault → env chain).

Both implement the Governance Pillar #3 "separate provider from secrets"
contract: they resolve secret VALUES by NAME at runtime, never hardcoding or
handing raw values around.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from src.capabilities.base import CapabilityAdapter, CapabilitySpec

logger = logging.getLogger(__name__)


class ConnectorProxyVaultAdapter(CapabilityAdapter):
    """Secrets via the Replit connector / integration proxy.

    The proxy resolves credentials for connected OAuth / API integrations
    (Google, Stripe, GitHub, …) without the operator manually copying keys.
    Available when REPLIT_CONNECTORS_HOSTNAME and a valid identity token are
    both present in the environment.
    """

    def _probe(self) -> bool:
        try:
            from src.web.connectors import proxy_available
            return proxy_available()
        except Exception:
            return False

    def health_probe(self) -> Dict[str, Any]:
        ok = self._probe()
        return {
            "ok": ok,
            "detail": "available (Replit connector proxy)" if ok
                      else "unavailable (REPLIT_CONNECTORS_HOSTNAME or identity token missing)",
            "exec_locus": self.exec_locus,
        }

    def resolve(self, secret_name: str) -> Optional[str]:
        """Resolve ``secret_name`` via the connector proxy.

        Returns None when the proxy is unavailable or the secret is not found,
        so callers fall through to the next vault adapter.
        """
        try:
            from src.web.connectors import get_connection_settings
            settings = get_connection_settings(secret_name)
            if settings and isinstance(settings, dict):
                for v in settings.values():
                    if v:
                        return str(v)
        except Exception as exc:
            logger.debug("connector proxy resolve failed for %s: %s", secret_name, exc)
        return None


class EnvVaultAdapter(CapabilityAdapter):
    """Secrets from the process environment (env_2 layer).

    This is the lowest-cost / always-available tier in the secret-resolution
    precedence chain. It wraps the existing ``src.providers.secrets`` module,
    which is the canonical env_2 access point — all secret reads go through it.
    """

    def _probe(self) -> bool:
        return True

    @property
    def exec_locus(self) -> str:
        return "local"

    def health_probe(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "detail": "available (process environment / Replit Secrets)",
            "exec_locus": "local",
        }

    def resolve(self, secret_name: str) -> Optional[str]:
        """Resolve ``secret_name`` from the process environment."""
        try:
            from src.providers.secrets import get_secret
            return get_secret(secret_name)
        except Exception:
            return None

    def has_secret(self, secret_name: str) -> bool:
        try:
            from src.providers.secrets import has_secret
            return has_secret(secret_name)
        except Exception:
            return False
