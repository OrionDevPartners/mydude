---
name: Anthropic skill siphon → MyDude design skills
description: Which Anthropic skills were reworked into MyDude (andydude) skills and why the rest were rejected.
---

# Anthropic skill siphon → MyDude design/creative skills

MyDude vendors reworked Anthropic Agent Skills under a `mydude-*` prefix (alongside the
pre-existing `mydude-glass-ui`). Source catalog = `github.com/anthropics/skills`
(`skills/<name>/SKILL.md`, 17 skills + a template).

## What was brought over (the design/creative cluster)
- `mydude-implement-design` — Figma/screenshot → glass-dark SPA code (adapts the Claude
  Code `/implement-design` Figma-plugin concept; not in the public repo).
- `mydude-frontend-design` — aesthetic direction (reworks Anthropic `frontend-design`).
- `mydude-theme-factory` — accent-duotone themes over the fixed glass-dark substrate.
- `mydude-brand-guidelines` — applies MyDude.io's brand (NOT Anthropic's) — name from
  `src/web/branding.py`, glass-dark palette, system/Arial fonts.
- `mydude-canvas-design` — static art (.png/.pdf) via code_execution (Pillow/reportlab).
- `mydude-algorithmic-art` — generative art (numpy/Pillow static, or vendored-p5 SPA viewer);
  siphoned as the semantic sibling of canvas-design.

## Why the rest were NOT siphoned
- Already vendored in `.agents/skills/`: `mcp-builder`, `pdf`, `pptx`, `skill-creator`,
  `web-artifacts-builder`. `webapp-testing` → MyDude's own `testing` skill. `xlsx` →
  `excel-generator` secondary skill.
- **`claude-api` rejected on governance pillar 2 (provider-agnostic):** it hardwires one
  provider; MyDude abstracts all LLMs behind the WaveOrchestrator/MultiProviderLLM swarm.
- Off-cluster (document/comms, not visual design): `docx`, `doc-coauthoring`,
  `internal-comms`, `slack-gif-creator`.

**Why:** the user asked to "scan ALL Anthropic skills and siphon"; semantic scope = the
design/creative cluster they named, plus algorithmic-art. Governance pillars constrain the
import (no provider-locked skills, no duplicates).

## How every reworked skill is adapted (the MyDude constraints baked in)
Tokens-only via `frontend/src/index.css` CSS vars + glass-* utilities (never raw hex);
`lucide-react` icons only; NO external CDN/fonts; every AI surface routes through the
governed WaveOrchestrator path (`src/swarm/orchestrator.py`) and stays provider-agnostic;
NO placeholders/mocks; preserve the dev sign-in gate; build/verify via
`scripts/build-frontend.sh` + the screenshot tool. They reference `mydude-glass-ui` for the
token table rather than duplicating it.
