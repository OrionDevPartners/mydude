---
name: mydude-frontend-design
description: >
  Aesthetic direction for building or reshaping MyDude.io SPA UI so it reads as
  intentional and distinctive while staying inside the glass-dark design language.
  Use when designing a new page/section or elevating an existing one — choosing
  hierarchy, typography, motion, and copy. Reworked from Anthropic's frontend-design
  for the andydude agent.
---

# MyDude Frontend Design

Approach this as the design lead for MyDude.io — a governance-first AI business
automation platform with an established **glass-dark** identity. MyDude already has
a point of view; your job is to **deepen and extend it with craft**, not to invent a
new look every time. Consistency with the existing language beats novelty. Take one
justified aesthetic risk per surface, never a templated default.

## Ground it in the subject first
Before designing, name the surface's single job, its operator (the user is a
technical operator running governed AI tasks), and the one thing they need first.
MyDude's world — swarms, waves, compliance scores, hallucination risk, provenance,
audit trails, capability brokers — is where distinctive choices come from. Let the
domain shape the design: a governance dashboard should *feel* like an instrument
panel, not a generic SaaS card grid.

## Work inside the glass-dark language
The substrate is fixed; express personality within it.
- **Palette:** deep space base (`--bg-base #050810`), glass surfaces, red accent
  (`--accent #e94560`) with violet secondary (`--accent-violet #7c5cbf`). Use accent
  sparingly — it marks the single most important action or state on a surface.
  Semantic colors (green/yellow/red/blue) carry meaning, never decoration.
- **Tokens only.** Everything via the CSS variables in `frontend/src/index.css` and
  the glass-* Tailwind utilities / primitives in `frontend/src/components/glass.tsx`.
  The full token table is in `mydude-glass-ui` — read it, don't restate it.
- **No external CDN, no external fonts.** Typography is the system stack already set
  on `body`. Carry personality through *scale, weight, letter-spacing, and rhythm*
  (see `.page-title`, `.stat-value`, `.form-label`, `.nav-section`), not by importing
  a display face. Set a clear type scale and use it consistently.
- **Icons:** `lucide-react` only.

## Design principles
- **The hero is a thesis.** Open each page with the most characteristic thing for
  that surface — a live governance score strip, a running wave, the task prompt —
  not a generic title + stat. A big number with a small label is the template
  answer; use it only when the number truly is the headline.
- **Structure is information.** Eyebrows, dividers, section labels (`.nav-section`,
  `.page-subtitle`) must encode something true. Only number steps (01/02/03) when
  the content is genuinely a sequence (a wave cycle, a debate round) — order has to
  carry meaning the operator needs.
- **Glass depth conveys hierarchy.** Use blur depth and border strength to layer:
  `glass-panel` for chrome (sidebar/modals), `glass-card` for content, `glass-card-glow`
  for the one element you want to pull forward. Don't flatten everything into equal cards.
- **Motion serves comprehension.** The existing keyframes (`fadeIn`, `fadeInUp`,
  `glowPulse`, `thinkingBounce`, `shimmer`) are the vocabulary. Animate state changes
  (a wave advancing, a score settling), not decoration. Respect `prefers-reduced-motion`.
- **Governed output looks governed.** Any model output surface must show its
  provenance/scores (use `ScoreBar`, `ai-score-pill`, reasoning/sources primitives).
  Never present raw ungoverned text — it must come through the WaveOrchestrator path.

## Writing in the design (copy is design material)
- Write from the operator's side of the screen. Name things by what the operator
  controls ("Run task", "Approve provision"), never by internal mechanics.
- Active voice; an action keeps its name through the whole flow (button "Publish" →
  toast "Published"). Sentence case, plain verbs, no filler.
- Failure and empty states give direction, not mood: say what happened and the next
  step, in the interface's voice. An empty screen invites the first action.

## Self-critique loop
Build, then look. `screenshot(type="app_preview", path="/route")` and critique your
own work — a picture is worth 1000 tokens. Channel Chanel: before shipping, remove
one accessory. Cut any element that doesn't serve the operator's job. Keep a short
note of what you tried so later passes build on it.

## Quality floor (non-negotiable)
Responsive to mobile, visible keyboard focus (`:focus-visible` is already styled),
reduced motion respected, sufficient contrast on glass. Build with
`bash scripts/build-frontend.sh` (must exit 0) and verify with a screenshot before
declaring done.
