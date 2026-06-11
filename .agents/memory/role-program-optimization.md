---
name: Cognitive-role prompt programs
description: How the swarm's cognitive-role prompts became governed/optimizable like the judge, and why runtime == optimization execution.
---

# Cognitive-role prompts as governed programs

The prompt engine optimizes the swarm's cognitive-role prompts (architect, skeptic,
evidence_validator, falsifier), not just `judge_synthesis`. Each is a ProgramSpec on a
shared `RoleAgent` signature; the role discipline lives in the LIVE DB instructions
(seeded from the existing `*_PROMPT` constants in `src/swarm/prompts.py` + a shared
worker-output contract), governed by the same versioning/approval/rollback gate.

**Rule: runtime execution must match optimization execution.**
**Why:** if a role prompt is optimized as a standalone DSPy Predict but used at runtime
only as a directive injected into a different (multi-provider) call, the optimizer is
scoring something the runtime never actually runs — train/serve skew that invalidates the
governance signal. So the swarm runs registered roles through the same single-call seam
(`runtime.run_role` → `run_program`) the optimizer trains against.
**How to apply:** the orchestrator's `_call_worker` routes an agent through `run_role`
when `specs.role_program_for(cognitive_role)` is registered, and DEGRADES to the existing
multi-provider `self.llm.call` path on any failure (no provider / parse error / unregistered).
The degraded path is the prior approved baseline, never an ad-hoc ungoverned prompt.

**To add another governed role:** one line in `_ROLE_TABLE` in `src/promptopt/specs.py`
(cognitive-role value → prompts.py constant name + description). Everything else
(signature mapping, seeding, API, dashboard, metric) is generic.

**Metric is spec-aware:** `metric.make_metric(output_field, sections)` /
`make_gepa_metric(...)` close over the program's OWN signature output field and required
sections; `service.run_optimizers` builds them from the spec. Role programs reuse the six
worker sections (RESULT/ARTIFACTS/CHECKS/RISKS/CAPABILITIES/COMPRESSED_HANDOFF) so
`_parse_worker` still parses their output. The old bare `metric`/`gepa_metric` remain as
judge defaults for back-compat.
