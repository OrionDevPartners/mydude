---
name: mydude-theme-factory
description: >
  Apply or generate a cohesive accent theme for the MyDude.io SPA while preserving
  the glass-dark substrate. Use when the user wants to recolor/restyle the app,
  pick a theme, create a custom palette, or rebrand the accent system. Reworked
  from Anthropic's theme-factory for the andydude agent.
---

# MyDude Theme Factory

MyDude has one fixed substrate — a **glass-dark** UI on a deep-space base — and a
swappable **accent system** (a primary + secondary duotone). A "theme" here is a set
of CSS-variable overrides applied to the design-token block in
`frontend/src/index.css`. The glass, blur, radii, shadows, and layout never change;
only the accent duotone and its derived glows/gradients do. This keeps every theme
on-brand and on-aesthetic.

## How theming works in this codebase
All color is driven by CSS variables in two places in `frontend/src/index.css`:
1. The `@theme { … }` block (Tailwind v4 tokens, e.g. `--color-accent`).
2. The `:root { … }` block (runtime vars, e.g. `--accent`, `--accent-hover`,
   `--accent-dim`, `--accent-glow`, `--accent-violet`, plus accent-derived shadows
   `--shadow-glow`, `--shadow-glow-violet`, `--border-accent`).

Components never hardcode color, so changing these variables re-themes the whole app.
**A few literal accent colors are baked into gradients** (e.g. `.btn-primary`,
`.nav-link.active`, `.ai-user-bubble`, the body ambient orbs, scrollbars). When you
change a theme you MUST also update those literal `rgba(233,69,96,…)` /
`rgba(124,92,191,…)` stops so they track the new accent — grep for the old rgba
values and replace them. Leaving them behind is a half-applied theme (a placeholder
state — forbidden by governance).

## Step 1 — Show the presets and get an explicit choice
Present these built-in themes (all preserve the glass-dark base; only the duotone
changes). Ask the user which to apply, or to describe a custom one. Wait for explicit
confirmation before editing.

| Theme | Primary (`--accent`) | Hover | Secondary (`--accent-violet`) | Mood |
|---|---|---|---|---|
| **Crimson Nebula** (current) | `#e94560` | `#ff5577` | `#7c5cbf` | Bold, governance-first (default) |
| **Cyan Circuit** | `#22d3ee` | `#5fe3f5` | `#6366f1` | Cool, technical, telemetry |
| **Amber Forge** | `#f5a524` | `#ffbe4d` | `#ef6f53` | Warm, industrial, operator |
| **Emerald Vault** | `#34d399` | `#5ee9b5` | `#2dd4bf` | Calm, finance, trust |
| **Violet Pulse** | `#a855f7` | `#c084fc` | `#6366f1` | Electric, creative, AI |
| **Rose Quartz** | `#fb7185` | `#ff90a0` | `#c084fc` | Soft, approachable |
| **Solar Flare** | `#ff6b35` | `#ff8556` | `#ffb627` | High-energy, alerts |
| **Arctic Steel** | `#60a5fa` | `#7fb6fb` | `#818cf8` | Crisp, enterprise |

To preview a theme before committing, you may temporarily apply it, run the build,
and `screenshot(type="app_preview", path="/")` so the user sees it in context.

## Step 2 — Apply the chosen duotone
For the selected theme, derive the full variable set from the primary `P`, hover `H`,
and secondary `S` hex values:

```
--accent: P;            --accent-hover: H;            --accent-violet: S;
--accent-dim: rgba(P, 0.15);     --accent-glow: rgba(P, 0.25);
--accent-violet-dim: rgba(S, 0.15);
--border-accent: rgba(P, 0.3);
--shadow-glow: 0 0 28px rgba(P, 0.2);
--shadow-glow-violet: 0 0 28px rgba(S, 0.2);
@theme: --color-accent: P; --color-accent-hover: H; --color-accent-violet: S;
```
Then update every baked-in literal rgba of the *old* accent (search the file for the
previous primary/secondary rgba triplets — e.g. `233,69,96` and `124,92,191` — and
replace with the new primary/secondary channels) across: `.btn-primary` gradient,
`.btn-primary:hover`, `.glass-card-glow`, `.nav-link.active` gradient + inset glow,
`.form-input:focus`, `.prompt-box:focus-within`, `.ai-user-bubble` gradient,
`.ai-avatar`, `.ai-thinking-dots`, `body::before` / `body::after` ambient orbs, and
the `@keyframes glowPulse` shadow stops.

Use a single editing pass (or `sed`/`edit replace_all`) so no stale stop survives.

## Step 3 — Create a custom theme (when no preset fits)
1. From the user's description, choose a primary hue, a brighter hover of the same
   hue, and a complementary secondary. Keep saturation high enough to read on the
   dark base and contrast AA-legible against glass surfaces.
2. Name it (two words describing the feel, in the style above).
3. Apply it via Steps 2, build, and screenshot for review/verification.
4. Only keep it after the user confirms it reads well in-context.

## Step 4 — Build & verify
```bash
bash scripts/build-frontend.sh        # must exit 0
```
`restart_workflow("Start application")` then `screenshot(type="app_preview", path="/")`
and a busy page (e.g. `/dashboard`) to confirm buttons, active nav, focus rings,
chat bubbles, and ambient orbs all track the new accent. No stray old-accent pixels.

## Rules
- Never alter the glass substrate (blur, radii, glass fills, shadows other than the
  accent glows) — themes are accent-only.
- Never introduce an external font or CDN as part of a theme.
- A theme is "done" only when *every* accent-derived surface tracks it. Partial
  application is a placeholder state and is not allowed.
