"""Shared Azure data-plane helpers for the MyDude post-provision wiring.

This module is the single seam the post-provision CLIs use to reach the
deployed (private) Azure services:

  * ``credential()``            — one portable identity for every call.
  * ``get_deployment_outputs()`` — live endpoints from the ARM deployment.
  * Key Vault get/set + DSN builder + ``hydrate_env_from_keyvault()``.

Governance pillars honored here:
  #2 provider-agnostic — call sites never hardcode an endpoint; they read the
     live ARM outputs.
  #3 separate provider from secrets — DSNs and the BCS secret are sourced from
     Key Vault at runtime (or env fallback), never hand-handled or committed.
  #1 no placeholders / fail loud — every helper raises a clear error instead of
     silently degrading.

It runs from INSIDE the Azure VNet (a jump box or a VNet-integrated container),
because the services are private (public network access disabled + private
endpoints). From outside the VNet the calls fail at the network boundary by
design — that failure is the correct, loud signal, not a bug.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional
from urllib.parse import quote

# ---------------------------------------------------------------------------
# Resource coordinates (control-plane identifiers, NOT secrets).
# These match infra/mydude/local/deploy.py and the deployed RG `mydude`.
# ---------------------------------------------------------------------------
RG_NAME = "mydude"
DEPLOYMENT_NAME = "mydude-stack"
PG_PORT = 5432

# Key Vault secret names (the names live here; the values live only in Key Vault).
KV_AGENTS_HOME_PG_DSN = "agents-home-pg-dsn"
KV_PROVIDER_HOME_PG_DSN = "provider-home-pg-dsn"
KV_BCS_IDEMPOTENCY_KEY = "bcs-idempotency-key"

# Mapping: Key Vault secret name -> process env var the app/migrator reads.
# This is the only place that translates a vault secret into the runtime env,
# so secret sourcing stays centralized (governance pillar #3).
KV_TO_ENV = {
    KV_AGENTS_HOME_PG_DSN: "PG_AGENTS_HOME_DSN",
    KV_PROVIDER_HOME_PG_DSN: "PG_PROVIDER_HOME_DSN",
    # The BCS gate authenticates its lease with BCS_LEASE_SECRET (see
    # infra/mydude/gates/bcs_gate/app.py + migrators/base.py V6 gate). The
    # "BCS idempotency key" secret is the shared value that namespaces/locks
    # idempotent claim submission, so it hydrates into BCS_LEASE_SECRET.
    KV_BCS_IDEMPOTENCY_KEY: "BCS_LEASE_SECRET",
}

# Postgres role used by each database's DSN. The writer role is the app/migrator
# identity (see governance/*_schema.sql). Its password is set by the migrator's
# credential bootstrap from PG_*_WRITER_PASSWORD.
DB_ROLE = {
    "agents_home": "agents_home_writer",
    "provider_home": "provider_home_writer",
}
DB_WRITER_PASSWORD_ENV = {
    "agents_home": "PG_AGENTS_HOME_WRITER_PASSWORD",
    "provider_home": "PG_PROVIDER_HOME_WRITER_PASSWORD",
}
DB_DSN_SECRET = {
    "agents_home": KV_AGENTS_HOME_PG_DSN,
    "provider_home": KV_PROVIDER_HOME_PG_DSN,
}


class AzureWiringError(RuntimeError):
    """Raised when a required input/credential/endpoint is missing."""


@lru_cache(maxsize=1)
def credential():
    """Return one portable Azure credential for control- and data-plane calls.

    Uses ``DefaultAzureCredential`` so the same code works with:
      * a user-assigned managed identity on a VNet jump box / container, and
      * the AZURE_* service-principal env secrets (EnvironmentCredential).

    Cosmos (key auth disabled) and Key Vault are AAD-only, so this must resolve
    to an identity that holds the right data-plane RBAC / access policy.
    """
    try:
        from azure.identity import DefaultAzureCredential
    except ImportError as e:  # pragma: no cover - dependency is declared
        raise AzureWiringError("azure-identity is not installed.") from e
    return DefaultAzureCredential(exclude_interactive_browser_credential=True)


def subscription_id() -> str:
    sub = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
    if not sub:
        raise AzureWiringError(
            "AZURE_SUBSCRIPTION_ID is not set; cannot read ARM deployment outputs."
        )
    return sub


@lru_cache(maxsize=1)
def get_deployment_outputs() -> dict:
    """Read the live ``mydude-stack`` deployment outputs as a flat dict.

    Returns keys: resourceGroupName, postgresServerFqdn, keyVaultUri,
    adlsAccountName, foundryEndpoint, aoaiEndpoint, cosmosAccountName,
    cosmosEndpoint, fabricCapacityName.
    """
    from azure.mgmt.resource.deployments import DeploymentsMgmtClient

    dmc = DeploymentsMgmtClient(credential(), subscription_id())
    dep = dmc.deployments.get(RG_NAME, DEPLOYMENT_NAME)
    outputs = dep.properties.outputs or {}
    return {k: spec.get("value") for k, spec in outputs.items()}


def _require_output(outputs: dict, key: str) -> str:
    val = outputs.get(key)
    if not val or (isinstance(val, str) and val.startswith("NOT_DEPLOYED")):
        raise AzureWiringError(
            "Deployment output '%s' is missing or NOT_DEPLOYED (got %r). "
            "Run `python3 infra/mydude/local/deploy.py status` to inspect." % (key, val)
        )
    return val


def postgres_fqdn(outputs: Optional[dict] = None) -> str:
    return _require_output(outputs or get_deployment_outputs(), "postgresServerFqdn")


def keyvault_uri(outputs: Optional[dict] = None) -> str:
    return _require_output(outputs or get_deployment_outputs(), "keyVaultUri")


def cosmos_endpoint(outputs: Optional[dict] = None) -> str:
    return _require_output(outputs or get_deployment_outputs(), "cosmosEndpoint")


def aoai_endpoint(outputs: Optional[dict] = None) -> str:
    return _require_output(outputs or get_deployment_outputs(), "aoaiEndpoint")


# ---------------------------------------------------------------------------
# Postgres DSN construction
# ---------------------------------------------------------------------------
def build_pg_dsn(fqdn: str, database: str, user: str, password: str,
                 sslmode: str = "require") -> str:
    """Build a libpq URI DSN. Azure PG Flexible Server requires SSL.

    The password is URL-encoded so special characters never break the URI.
    """
    if not (fqdn and database and user and password):
        raise AzureWiringError(
            "build_pg_dsn requires fqdn, database, user and password (no empties)."
        )
    return "postgresql://%s:%s@%s:%d/%s?sslmode=%s" % (
        quote(user, safe=""), quote(password, safe=""), fqdn, PG_PORT, database, sslmode,
    )


def build_db_dsn(db_key: str, outputs: Optional[dict] = None) -> str:
    """Build the writer DSN for ``agents_home`` / ``provider_home`` from env+outputs."""
    if db_key not in DB_ROLE:
        raise AzureWiringError("Unknown database '%s'." % db_key)
    pwd_env = DB_WRITER_PASSWORD_ENV[db_key]
    password = os.environ.get(pwd_env, "")
    if not password:
        raise AzureWiringError(
            "%s is not set; cannot build the %s writer DSN. Set the writer "
            "role password env var first." % (pwd_env, db_key)
        )
    return build_pg_dsn(
        fqdn=postgres_fqdn(outputs),
        database=db_key,
        user=DB_ROLE[db_key],
        password=password,
    )


# ---------------------------------------------------------------------------
# Key Vault
# ---------------------------------------------------------------------------
def kv_client(vault_uri: Optional[str] = None):
    from azure.keyvault.secrets import SecretClient

    return SecretClient(vault_url=vault_uri or keyvault_uri(), credential=credential())


def kv_set_secret(name: str, value: str, vault_uri: Optional[str] = None) -> str:
    """Set a Key Vault secret. Returns the new version id (never the value)."""
    if not value:
        raise AzureWiringError("Refusing to set Key Vault secret '%s' to an empty value." % name)
    client = kv_client(vault_uri)
    prop = client.set_secret(name, value)
    return prop.properties.version


def kv_get_secret(name: str, vault_uri: Optional[str] = None) -> Optional[str]:
    from azure.core.exceptions import ResourceNotFoundError

    client = kv_client(vault_uri)
    try:
        return client.get_secret(name).value
    except ResourceNotFoundError:
        return None


def hydrate_env_from_keyvault(vault_uri: Optional[str] = None,
                              overwrite: bool = False) -> dict:
    """Load DSN + BCS secrets from Key Vault into ``os.environ``.

    This is the runtime secret-sourcing seam: the migrator, doctor, jurisdiction
    router and app read ``PG_AGENTS_HOME_DSN`` / ``PG_PROVIDER_HOME_DSN`` /
    ``BCS_LEASE_SECRET`` from the environment; this populates them from the vault
    without ever printing a value. Existing env values win unless ``overwrite``.

    Returns a dict of {env_var: status} where status is loaded/skipped/missing.
    """
    uri = vault_uri or keyvault_uri()
    result: dict = {}
    for secret_name, env_var in KV_TO_ENV.items():
        if os.environ.get(env_var) and not overwrite:
            result[env_var] = "skipped (already set)"
            continue
        val = kv_get_secret(secret_name, uri)
        if val:
            os.environ[env_var] = val
            result[env_var] = "loaded from %s" % secret_name
        else:
            result[env_var] = "missing in vault (%s)" % secret_name
    return result
