---
name: mydude-algorithmic-art
description: >
  Create original generative / algorithmic art for MyDude.io — flow fields, particle
  systems, noise fields, parametric compositions — as reproducible static exports
  (.png/.svg) or as a self-contained in-SPA interactive viewer. Use when the user
  asks for generative art, algorithmic art, or code-driven visuals. Reworked from
  Anthropic's algorithmic-art for the andydude agent. Create original work, never
  copy existing artists.
---

# MyDude Algorithmic Art

Generative art is a **computational aesthetic** expressed through code with seeded
randomness. Two steps: (1) write an **algorithmic philosophy** (`.md`), then (2)
express it as a generative algorithm. This is the code-driven sibling of
`mydude-canvas-design` (static, hand-composed art) — reach for this skill when the
beauty comes from process, emergence, and parametric variation.

## Two delivery targets (pick per the request)
1. **Static export (default):** generate reproducible `.png`/`.svg` in the
   `code_execution` sandbox with Python — `numpy` for fields/forces + `Pillow` for
   raster, or `svgwrite` for vector. No browser, no CDN. Best for posters, covers,
   batch variations.
2. **In-SPA interactive viewer:** a glass-dark page with live parameter controls.
   The generative library (e.g. `p5.js`) MUST be **vendored locally** (added to
   `frontend/package.json`, imported via `@/`) — **never loaded from a CDN** (hard
   MyDude rule). Mount it inside a `glass-card` page wired into `App.tsx` + the
   `Layout.tsx` NAV, with `lucide-react` controls and tokens-only styling.

## Governance (MyDude rules)
- **Seeded & reproducible.** Every run takes an explicit seed; the same seed always
  produces the same image. Record the seed in the philosophy `.md`.
- **No placeholders.** Ship a real, runnable algorithm and a real exported artifact
  (or a working SPA viewer). Install missing packages via package-management; fail
  loud rather than stub.
- **Governed text.** If an LLM writes the philosophy or any on-canvas labels, route
  it through the WaveOrchestrator path (provider-agnostic). No raw ungoverned output.
- **Brand default:** for MyDude-branded pieces use the palette (base `#050810`,
  crimson `#e94560`, violet `#7c5cbf`, ink `#eef0f6`); pure-art briefs get full freedom.
- **Original only** — never reproduce a living artist's signature style.

## Step 1 — Algorithmic Philosophy (.md)
Name the movement (1–2 words) and write 4–6 paragraphs describing the *computational
aesthetic*: the emergent behavior, the mathematics, the role of noise/forces, the
parametric range, and how craftsmanship shows up (density, restraint, calibration).
Emphasize master-level execution. Note the parameters and seed.

## Step 2 — Express as a generative algorithm
Static export skeleton (`code_execution`, Python):
```python
import numpy as np
from PIL import Image
rng = np.random.default_rng(seed=20260615)     # seeded → reproducible
W, H = 1600, 2000
img = np.zeros((H, W, 3), dtype=np.uint8); img[:] = (5, 8, 16)   # deep-space base
# flow field example: perlin/noise-driven angles → trace particles → accumulate
# ... evolve N agents over T steps, deposit crimson/violet strokes per the philosophy ...
Image.fromarray(img).save("attached_assets/algo-art/<movement>.png")
```
Principles: emergence over hand-placement; layered accumulation that rewards close
viewing; a limited cohesive palette; everything contained within margins; expose the
key parameters (agent count, step size, noise scale, palette) so variations are easy.

For the **SPA viewer**, build a page that runs the same algorithm with sliders/seed
input (glass-dark controls), a canvas element, and an export-PNG button — all
self-contained, library vendored locally.

## Step 3 — Refine & vary
Tune parameters for cohesion, not clutter. Offer a small seeded series (same
algorithm, different seeds) when the user wants options. Refine the existing
composition before adding new mechanisms.

## Step 4 — Deliver
- Static: present the `.png`/`.svg` with `present_asset`.
- SPA viewer: `bash scripts/build-frontend.sh` (exit 0), restart, and
  `screenshot(type="app_preview", path="/your-route")` to verify it renders.

## Rules
- Seeded and reproducible, always.
- No CDN — vendor any in-browser library locally.
- Real algorithm + real output; no stubs. Original work only.
