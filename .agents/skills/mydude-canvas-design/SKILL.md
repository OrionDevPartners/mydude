---
name: mydude-canvas-design
description: >
  Create original, gallery-quality visual art as .png or .pdf for MyDude.io —
  posters, hero art, report covers, social cards — driven by a written design
  philosophy. Use when the user asks for a poster, artwork, cover, or static
  design piece. Reworked from Anthropic's canvas-design for the andydude agent.
  Never copy existing artists' work.
---

# MyDude Canvas Design

Produce a single, design-forward static piece (`.png` or `.pdf`) that is 90% visual,
10% essential text. Two steps: (1) write a **design philosophy** (`.md`), then (2)
**express it on a canvas**. Always create original work — never reproduce a living
artist's style or copyrighted imagery.

## Governance & environment (MyDude rules)
- **Render with real tools, no CDN, no placeholders.** Generate the artifact in the
  `code_execution` sandbox with Python (`Pillow`, `matplotlib`, `svgwrite`/`cairosvg`
  for vector, `reportlab` for multi-page PDF) — install via the package-management
  skill if missing. Do not embed remote/CDN assets and do not output a half-rendered
  "draft" as final.
- **Governed text/concepts.** If you use an LLM to generate the philosophy, the
  conceptual thread, or any on-canvas copy, route it through the governed
  WaveOrchestrator path (provider-agnostic) — never paste raw ungoverned model text
  onto the canvas.
- **Fonts:** use locally available system fonts (or fonts you download into the
  sandbox for this render). Never depend on a web-font CDN at view time — the output
  is a flattened PNG/PDF, so fonts are baked in at render.
- **Brand alignment (default, not a cage):** unless the brief says otherwise, lean on
  the MyDude palette (deep-space base `#050810`, crimson `#e94560`, violet `#7c5cbf`,
  ink `#eef0f6`) for anything MyDude-branded. Pure-art briefs get full creative
  freedom — see `mydude-brand-guidelines` when brand fidelity matters.

## Step 1 — Design Philosophy (.md)
Create a **visual philosophy** (an aesthetic movement), not a layout. Output a `.md`.

1. **Name the movement** (1–2 words): e.g. "Concrete Poetry", "Chromatic Silence".
2. **Write 4–6 substantial paragraphs** describing how it manifests through: space &
   form · color & material · scale & rhythm · composition & balance · visual hierarchy.
3. Emphasize, repeatedly, that the final work must look **meticulously crafted** — the
   product of deep expertise, painstaking attention, master-level execution.
4. Avoid redundancy (state each idea once, add depth not repetition). Keep it generic
   enough that the next pass has interpretive room, specific enough to guide direction.
5. Text is sparse, essential-only, integrated as a visual element — never paragraphs
   on the canvas.

## Step 2 — Deduce the subtle reference
Before rendering, identify the **subtle conceptual thread** from the request. Weave it
invisibly into form, color, and composition — like a jazz musician quoting another
song: those who know will catch it, everyone else simply experiences a masterful
composition. Never make it literal or loud.

## Step 3 — Express it on the canvas
Using the philosophy + the deduced thread, render one highly visual, design-forward
page (PNG or PDF). Guidelines:
- Repeating patterns, precise shapes, systematic marks; build meaning through patient
  accumulation that rewards sustained viewing.
- Sparse, clinical typography and reference markers — as if a diagram from an
  imaginary discipline. Thin weights by default; type can be bold when the context
  (e.g. a punk poster) calls for it. Make typography part of the art, not typeset-on-top.
- A limited, intentional, cohesive palette.
- **Containment is non-negotiable:** nothing falls off the page, nothing overlaps,
  every element has breathing room and clear margins.
- Seed any randomness so the result is reproducible (note the seed in the `.md`).

Example render skeleton (run in `code_execution`):
```python
from PIL import Image, ImageDraw, ImageFont
W, H = 1620, 2160          # 3:4 poster @ ~270dpi
img = Image.new("RGB", (W, H), (5, 8, 16))   # deep-space base
d = ImageDraw.Draw(img)
# ... systematic marks, shapes, restrained type per the philosophy ...
img.save("attached_assets/canvas/<movement>.png")
```

## Step 4 — Refine to "pristine"
Treat the first output as not-yet-perfect. Do a second pass: do **not** add more
graphics — make what exists crisper and more cohesive. If the instinct is to draw a
new shape, stop and ask "how do I make what's already here more of a piece of art?"
Verify: nothing overlaps, margins clean, color and type cohesive, masterful.

## Step 5 — Deliver
- Output the final `.png`/`.pdf` plus the philosophy `.md`.
- Present the artifact with `present_asset` (it's a non-code deliverable the user
  requested). Do not present the `.md` unless asked.

## Multi-page option
If asked for more pages, treat page one as the opening of a coffee-table book: each
new page a distinct twist on the same philosophy, tastefully telling a story. Bundle
as one multi-page PDF or several PNGs.

## Rules
- Original work only — no copying living artists or copyrighted material.
- Sophisticated always — even for movie/game/book briefs, never cartoony or amateur.
- Real render, real file, no placeholder graphics. Fail loud if a tool is missing
  (then install it) rather than shipping a stub.
