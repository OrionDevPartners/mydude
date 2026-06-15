---
name: mydude-implement-design
description: >
  Convert a provided visual design (Figma frame, screenshot, mockup, or image)
  into production glass-dark code for the MyDude.io SPA. Use when the user shares
  a Figma URL, a screenshot, or a design reference and wants it implemented as
  real React + Tailwind v4 components in frontend/src. Reworked from Anthropic's
  /implement-design (Figma plugin) workflow for the andydude agent.
---

# MyDude Implement Design

Faithfully translate a **given** design into MyDude's glass-dark SPA. This skill is
about *reproduction with fidelity*, not inventing a new aesthetic — for net-new
aesthetic direction use `mydude-frontend-design`; for the token system and build
loop use `mydude-glass-ui` (this skill builds on it, never duplicates it).

## Governance (always enforce — MyDude pillars)
- **No placeholders / mocks.** Ship the real, wired component. If a data source is
  missing, wire it to the real `/api` endpoint or fail loud — never fake data.
- **Design tokens only.** Never hardcode `#e94560` or `16px`; use the CSS variables
  in `frontend/src/index.css` (`var(--accent)`, `var(--glass-blur)`, …) and the
  glass-* Tailwind utilities. Full token table lives in `mydude-glass-ui`.
- **Icons:** `lucide-react` only. No icon CDNs, no SVG sprites, no external fonts.
- **AI surfaces are governed.** Any panel that shows model output must consume it
  from the existing governed pipeline (`src/swarm/orchestrator.py` WaveOrchestrator,
  the same path the dashboard task runner uses) via the `/api` layer. Never call an
  LLM provider directly from a component, and never bake in one provider.
- **Preserve the dev sign-in gate.** Do not touch `src/web/auth.py` affordances.

## Step 1 — Acquire the design context

**If it's a Figma URL** — use the Figma MCP via `code_execution` (see
`.local/mcp_skills/figma/SKILL.md`):

```javascript
const ctx = await mcpFigma_getDesignContext({
  fileKey: "ABCDEF",          // figma.com/design/ABCDEF/... 
  nodeId: "12:34",            // ?node-id=12-34  →  "12:34"
  clientLanguages: "typescript",
  clientFrameworks: "react",
});
// ctx.content → reference JSX, a screenshot, token values, asset URLs
```
Download any returned asset URLs immediately into `frontend/src/assets/`.

**If it's a screenshot / image** — read it with the `read` tool (it renders images)
and treat it as the source of truth for layout, spacing, hierarchy, and color
intent. Map observed colors to the *nearest* existing token, do not introduce new
raw hex unless the design demands a new brand color (then add it as a token first).

## Step 2 — Decompose before you build
List, in one pass: the page/route, the regions (header, content, aside), each
repeated unit (cards, rows), the interactive elements, and every place text or a
number comes from real data. Decide for each datum: which `/api` call in
`frontend/src/lib/api.ts` already provides it, or whether a new endpoint is needed.

## Step 3 — Map design → glass-dark primitives
| In the design | Use in MyDude |
|---|---|
| Card / panel | `GlassCard` / `.glass-card` (auto blur + border + shadow) |
| Primary button | `<button className="btn btn-primary">` or `GlassButton variant="primary"` |
| Input / textarea / select | `.form-input` (+ `.form-label`, `.form-group`) |
| Status pill | `.badge` + semantic modifier, or `GlassBadge color=…` |
| Banner / callout | `.alert alert-{success,error,warn,info}` |
| Table | `.data-table` |
| Stat tile | `.stat-card` + `.stat-value` / `.stat-label` |
| Chat / model output | compose from `@/components/ai-elements` (`MessageThread`, `AssistantMessage`, `ScoreBar`, …) |
| Icon | `import { Name } from 'lucide-react'` |

Match spacing/radii to the radius scale (`--radius-sm/md/lg/xl`) rather than the
design's raw pixel values when they're within a few px — consistency beats literal
fidelity for spacing.

## Step 4 — Wire it into the app
1. New page → `frontend/src/pages/MyPage.tsx`.
2. Register the lazy route in `frontend/src/App.tsx`.
3. Add the nav entry to the `NAV` array in `frontend/src/components/Layout.tsx`
   (place it in the section that matches the user journey).
4. New data → add a typed client fn in `frontend/src/lib/api.ts` and a real backend
   route under `src/web/api/router.py`. No mock responses.

## Step 5 — Build & verify (closed loop)
```bash
bash scripts/build-frontend.sh        # cd frontend && npm run build → static/spa ; must exit 0
```
Then restart and screenshot to compare against the source design:
- `restart_workflow("Start application")`
- `screenshot(type="app_preview", path="/your-route")`

Compare side-by-side with the design. Iterate on spacing, contrast, and alignment
until it matches. A picture is worth 1000 tokens — always screenshot before calling
it done.

## Fidelity checklist
- [ ] Layout, hierarchy, and spacing match the source within the radius/spacing scale
- [ ] Every color is a token; no stray raw hex
- [ ] All icons from `lucide-react`; no CDN assets or external fonts
- [ ] Real data wired (or a real new `/api` endpoint), zero placeholders
- [ ] AI output (if any) flows through the governed orchestrator path
- [ ] `build-frontend.sh` exits 0 and the screenshot matches the design
- [ ] Responsive down to mobile; visible keyboard focus; reduced motion respected
