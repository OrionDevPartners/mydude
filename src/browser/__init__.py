"""Browser capability package — vendor-agnostic browser automation.

Mirrors src/providers/: call sites drive a browser through ``BrowserEngine``
without ever naming a vendor. Backends (local Chromium, Browserbase, ...) are
selected and cost-ordered in config/providers.toml. Heavy deps are imported
lazily so the app boots with this capability disabled and the vault empty.
"""
