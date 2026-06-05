"""Provider- and environment-agnostic abstraction layer.

Three-layer separation:
  * Code (this package's interfaces + the call sites) never names a vendor.
  * env_1 (config/providers.toml, committed) maps capability -> provider and
    declares which secret NAMES each provider needs.
  * env_2 (Replit Secrets / the credential vault, never committed) holds the
    secret VALUES, read only through src.providers.secrets.
"""
