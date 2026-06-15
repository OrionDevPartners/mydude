---
name: mydude-brand-guidelines
description: >
  Apply MyDude.io's official brand identity — name, voice, colors, and typography —
  to any artifact (SPA UI, slides, docs, exported reports, social images). Use when
  brand colors, visual formatting, tone of voice, or company design standards apply.
  Reworked from Anthropic's brand-guidelines for the andydude agent.
---

# MyDude.io Brand Styling

Apply MyDude.io's look-and-feel and voice consistently across everything andydude
produces. This is the brand analog of MyDude's glass-dark UI tokens — use it for
artifacts *outside* the SPA (slides, PDFs, docs, social cards) and to keep in-app
copy on-brand.

**Keywords**: branding, corporate identity, visual identity, styling, brand colors,
typography, MyDude brand, voice and tone, visual formatting.

## Name & tagline (single source of truth)
Defined in `src/web/branding.py` — read it, never hardcode the name elsewhere.
- **Product name:** `MyDude.io` · **Short:** `MyDude` · deployed at **mydude.io**
- **Tagline:** "AI Business Automation Platform"
- Positioning: governance-first AI — epistemic discipline, transparent decisions,
  audit trails. The brand should feel like a precision instrument, not a toy.

## Colors
The brand is **dark-first**. Light artifacts are the exception; default to the
glass-dark palette.

**Core:**
- Deep Space (base) `#050810` — primary background
- Deep Slate `#080d1a` — panels / deepest surfaces
- Ink (text) `#eef0f6` — primary text on dark
- Muted `#8892a4` — secondary text/labels
- Faint `#4a5568` — placeholders / disabled

**Accent (duotone):**
- Crimson `#e94560` — primary accent, the single most important action/state
- Crimson Bright `#ff5577` — hover/active
- Violet `#7c5cbf` — secondary accent, AI/reasoning surfaces

**Semantic (status only, never decoration):**
- Green `#34d399` · Yellow `#fbbf24` · Red `#f87171` · Blue `#60a5fa` · Purple `#a78bfa`

Glass surfaces are white at very low alpha over the dark base
(`rgba(255,255,255,0.035)` fill, `rgba(255,255,255,0.08)` border) with backdrop blur.
In the SPA these are tokens (`var(--accent)`, `var(--bg-glass)`, …) — always use the
token, never the literal hex. In external artifacts use the hex above.

## Typography
- **No external fonts, no CDN** (hard rule — matches the SPA). Use the system stack:
  `-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Inter, sans-serif`.
- **Monospace** (code, scores, ledgers): `'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace`.
- Personality comes from **scale, weight, and letter-spacing**, not a display face:
  - Page title: 21px / 700 / `-0.3px`
  - Big stat: 28px / 700
  - Section/eyebrow label: ~10–11.5px / 700 / uppercase / `+0.06–0.12em` letter-spacing
  - Body: 13–15px / 400–500 / line-height ~1.65
- For artifacts where fonts must be embedded (PDF/PPTX) and the system stack is
  unavailable, fall back to **Arial/Helvetica** (sans) and keep the same scale and
  weights. Never substitute a decorative web font.

## Shape & accent language
- Rounded corners on the radius scale: 8 / 12 / 18 / 24 px (sm→xl).
- Accent used sparingly: one crimson element per view marks the priority; violet
  marks AI/reasoning. Status colors only where they carry state.
- Ambient depth: subtle radial glows (crimson and violet at very low alpha) on dark
  backgrounds evoke the SPA's ambient orbs — optional for hero artifacts.

## Voice & tone
- **Precise, calm, governed.** Plain verbs, active voice, sentence case. Specific
  over clever. Name things by what the operator controls.
- **Transparent about uncertainty.** Never overclaim. Where a claim is model-derived,
  the brand surfaces its governance (compliance score, hallucination risk, provenance)
  rather than hiding it. Errors explain what happened and the next step.
- **Operator-first.** The audience is a technical operator running governed AI work,
  not a consumer. Respect their time; lead with the action or the number that matters.

## Applying the brand
- **In the SPA:** use the glass-dark tokens/primitives — see `mydude-glass-ui` and
  `mydude-frontend-design`. Brand = tokens; do not hardcode hex.
- **In slides/docs/exports:** apply the hex palette above, the system/Arial type
  scale, the radius scale, dark-first backgrounds, and the voice rules. For decks use
  the `slides`/`pptx` skills; for documents the `pdf` skill — feed them this palette
  and type scale.
- **Any AI-generated brand copy or imagery** must route through the governed
  WaveOrchestrator path (no raw ungoverned output reaching an artifact) and stay
  provider-agnostic.

## Checklist
- [ ] Name/tagline pulled from `src/web/branding.py`; not hardcoded
- [ ] Dark-first; palette hexes (or SPA tokens) exact
- [ ] System/Arial type only — no external font or CDN
- [ ] Accent used sparingly; status colors only for state
- [ ] Voice: precise, active, transparent about model uncertainty
