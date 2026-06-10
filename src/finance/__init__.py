"""Finance / accountant sub-stack (QuickBooks + Plaid).

Read-only by default. Postgres is the system of record for raw financial data;
only relation-level claims (vendor -> project edges, aggregates) are written to
the shared memory substrate. Every write to QuickBooks goes through a two-phase
approval gate (see ``writeback.py``) and is audited.
"""
