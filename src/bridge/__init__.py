"""Bridge package — connectors to the user's own machine(s).

Currently provides an SSH bridge to the user's Mac for running whitelisted
commands and reading local artifacts (browser history, recent verification
codes). All access is broker/policy-gated and audit-logged; paramiko is imported
lazily so the app boots with this capability disabled and the vault empty.
"""
