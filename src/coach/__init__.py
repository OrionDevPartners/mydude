"""Coach / Secretary / Mood sub-stack.

A personal-assistant (secretary) + empathetic life-coach + mood layer for
MyDude.io. Mirrors the finance sub-stack patterns:

  - providers.py   credential sourcing (connector proxy -> vault) + honest status
  - client_hume.py concrete emotion provider client (httpx, fail loud)
  - sentiment.py   text sentiment via the governed LLM swarm
  - behavior.py    behavioral signals (calendar density, financial stress)
  - ingestion.py   normalize signals -> MoodSignal rows + LOCAL-ONLY memory nodes
  - coach.py       grounded, cited coaching answers (fail loud, never fabricate)
  - reflection.py  periodic pattern surfacing -> CoachInsight
  - scheduler.py   opt-in reflection loop
  - secretary.py   two-phase, approval-gated outbound actions
  - delivery.py    provider-agnostic dispatch (email/sms/calendar), fail loud

Governance: emotional/personal data is highly sensitive. Emotion/behavior nodes
are written to memory with ``local_only=True`` (Private-Mode) so they never
egress via the cloud adapter. Postgres is the system of record; nodes are
purgeable. No outbound action is taken without explicit operator approval.
"""
