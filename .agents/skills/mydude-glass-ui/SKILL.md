---
name: mydude-glass-ui
description: >
  Design and build MyDude.io UI using Figma (via MCP), v0 API, and AI SDK Elements.
  Use when designing or implementing any UI for the MyDude SPA: new pages, component
  redesigns, glass-dark primitives, AI chat surfaces, or shell updates.
---

# MyDude Glass UI Skill

Concrete, runnable workflow for designing and building MyDude.io UI components using
**Figma MCP → v0 API → React + Tailwind v4 + AI SDK Elements + glass-dark design system**.

---

## 0. Constraints (always enforce)

- **No external CDN at runtime.** All components are vendored locally.
- **No hardcoded secrets.** Figma token = `FIGMA_API_KEY` secret. v0 token = `V0_API_KEY` secret. Read via `import.meta.env` or server-side `os.environ` — never inline.
- **Single canonical component set.** Use `frontend/src/components/ai-elements/` for AI-chat primitives and `frontend/src/components/glass.tsx` for glass primitives. Never duplicate.
- **Design tokens only via CSS variables.** Never hardcode `#e94560`; write `var(--accent)`. Never hardcode blur values; write `var(--glass-blur)`.
- **Governance rule (provider-agnostic).** Any new AI surface must wire through the existing `WaveOrchestrator` path — no direct `fetch` to an LLM.

---

## 1. Read Figma design context

Use the **Figma MCP server** (available via `code_execution` callbacks).

```javascript
// Step 1 — confirm you're authenticated
const me = await mcpFigma_whoami({});
console.log(me);

// Step 2 — get page list if you only have a file key
const meta = await mcpFigma_getMetadata({ fileKey: "YOUR_FILE_KEY" });
console.log(meta);

// Step 3 — get design context for a specific node
const ctx = await mcpFigma_getDesignContext({
  fileKey: "YOUR_FILE_KEY",
  nodeId: "1:23",              // extract from Figma URL ?node-id=1-23 → "1:23"
  clientLanguages: "typescript",
  clientFrameworks: "react",
});
console.log(ctx);
// ctx.content contains: reference code, screenshot, token values, asset URLs
```

**How to extract IDs from a Figma URL:**
- `https://figma.com/design/ABCDEF/MyFile?node-id=12-34` → `fileKey="ABCDEF"`, `nodeId="12:34"`

**What to do with the response:**
- `ctx.content.code` — reference JSX; adapt it to this project's conventions (CSS vars, Tailwind v4 utilities, no inline RGB).
- `ctx.content.screenshot` — visual reference; attach to your context for accuracy.
- Asset download URLs — download promptly and place in `frontend/src/assets/`.

---

## 2. Generate / iterate with v0

v0 produces Tailwind + shadcn/ui code. Adapt it to MyDude conventions after generation.

```typescript
// Read the key at runtime — never hardcode
const V0_KEY = process.env.V0_API_KEY ?? (() => { throw new Error('V0_API_KEY not set') })()

async function generateWithV0(prompt: string): Promise<string> {
  const res = await fetch('https://v0.dev/api/generate', {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${V0_KEY}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt, framework: 'react', styling: 'tailwind' }),
  })
  if (!res.ok) throw new Error(`v0 error ${res.status}: ${await res.text()}`)
  const json = await res.json()
  return json.code as string
}
```

**Adaptation checklist (always apply after v0 output):**
1. Replace all `bg-zinc-*`, `bg-slate-*`, `bg-gray-*` with `var(--bg-*)` or `bg-glass-*` Tailwind tokens.
2. Replace hardcoded accent colors with `var(--accent)` / `text-accent`.
3. Replace `rounded-md` → `rounded-xl` / `rounded-2xl` per the radius scale.
4. Add `backdrop-blur-glass` + `border-glass` to any card/panel.
5. Strip any `import` of a CDN font or icon library — use `lucide-react` only.
6. Replace shadcn `Button` → `<button className="btn btn-primary">` or the `GlassButton` primitive.
7. Wrap AI output surfaces in the existing `AssistantMessage` / `MessageThread` primitives.

---

## 3. Glass-dark design system

### Design tokens (CSS variables in `frontend/src/index.css`)

| Variable | Value | Usage |
|---|---|---|
| `--bg-base` | `#050810` | Page background |
| `--bg-deep` | `#080d1a` | Deepest surfaces |
| `--bg-glass` | `rgba(255,255,255,0.035)` | Glass card fill |
| `--bg-glass-hover` | `rgba(255,255,255,0.06)` | Hover state |
| `--bg-glass-active` | `rgba(255,255,255,0.09)` | Active/pressed |
| `--glass-blur` | `16px` | Standard backdrop blur |
| `--glass-blur-heavy` | `28px` | Modal/overlay blur |
| `--border-glass` | `rgba(255,255,255,0.08)` | Glass border |
| `--border-glass-strong` | `rgba(255,255,255,0.14)` | Stronger divider |
| `--accent` | `#e94560` | Brand red |
| `--accent-glow` | `rgba(233,69,96,0.25)` | Glow halos |
| `--accent-violet` | `#7c5cbf` | Secondary accent |
| `--text-primary` | `#eef0f6` | Body text |
| `--text-secondary` | `#8892a4` | Muted label |
| `--text-muted` | `#4a5568` | Placeholder / disabled |
| `--radius-sm` | `8px` | Buttons, inputs |
| `--radius-md` | `12px` | Cards |
| `--radius-lg` | `18px` | Modals, panels |
| `--shadow-glass` | `0 8px 32px rgba(0,0,0,0.4)` | Card shadow |
| `--shadow-glow` | `0 0 24px var(--accent-glow)` | Accent glow |

### Tailwind v4 tokens (defined via `@theme` in `index.css`)

```css
@theme {
  --color-accent: #e94560;
  --color-accent-violet: #7c5cbf;
  --color-bg-glass: rgba(255,255,255,0.035);
  --color-bg-glass-hover: rgba(255,255,255,0.06);
  --blur-glass: 16px;
  --blur-glass-heavy: 28px;
  --radius-glass-sm: 8px;
  --radius-glass-md: 12px;
  --radius-glass-lg: 18px;
}
```

Use as Tailwind utilities: `bg-bg-glass`, `blur-glass`, `rounded-glass-md`, `text-accent`.

### Core glass primitives (in `frontend/src/components/glass.tsx`)

```tsx
import { GlassPanel, GlassCard, GlassButton, GlassInput, GlassBadge } from '@/components/glass'
```

| Component | Props | Notes |
|---|---|---|
| `GlassPanel` | `className?, children` | Full-bleed glass surface (sidebar, modals) |
| `GlassCard` | `className?, padding?, children` | Standard content card |
| `GlassButton` | `variant='primary'|'secondary'|'ghost'|'danger', size='sm'|'md'` | All button styles |
| `GlassInput` | `...InputHTMLAttributes` | Styled form input |
| `GlassBadge` | `color='green'|'red'|'yellow'|'blue'|'purple'|'gray'` | Semantic badge |

---

## 4. AI SDK Elements (vendored set)

All AI chat primitives live in `frontend/src/components/ai-elements/`. Import from the barrel:

```tsx
import {
  PromptInput, PromptInputBody, PromptInputTextarea,
  PromptInputActions, PromptInputActionSend,
  MessageThread,
  UserMessage,
  AssistantMessage,
  ReasoningMessage,
  SourcesMessage,
  CodeBlock,
  ThinkingIndicator,
  ScoreBar,
  Message, MessageContent, MessageActions, MessageAction,
  MessageBranch, MessageBranchContent, MessageBranchSelector,
  MessageBranchPrevious, MessageBranchNext, MessageBranchPage,
  MessageResponse, MessageToolbar,
} from '@/components/ai-elements'
```

**Do not install additional AI SDK Elements packages at runtime.** The vendored set is
the single canonical source. If upstream `ai-elements@npm` adds new components, vendor
them here and re-export from `index.ts`.

To check the upstream package for new exports (dev-time only):
```bash
node -e "console.log(Object.keys(require('./node_modules/ai-elements')))"
```

---

## 5. Step-by-step workflow for a new UI feature

```
1. Get Figma context
   ↳ mcpFigma_getDesignContext({ fileKey, nodeId })
   ↳ Review screenshot + reference code

2. Draft with v0 (optional)
   ↳ generateWithV0("Dark glass card for [feature], Tailwind v4, no CDN")
   ↳ Apply adaptation checklist (§2)

3. Implement in project
   a. If a new page → create frontend/src/pages/MyPage.tsx
      Add route to frontend/src/App.tsx
      Add nav item to frontend/src/components/Layout.tsx NAV array
   b. If a reusable primitive → add to frontend/src/components/glass.tsx
   c. If an AI chat surface → compose from @/components/ai-elements
   d. Design tokens only via CSS vars / Tailwind glass-* utilities (never hardcode)

4. Verify
   ↳ cd frontend && npm run build   (must exit 0)
   ↳ restart_workflow("Start application") + screenshot

5. Register Code Connect (optional, for Figma sync)
   ↳ mcpFigma_addCodeConnectMap({ fileKey, nodeId, source, componentName, label:"React" })
```

---

## 6. Pitfalls & rules

- **backdrop-filter requires a non-transparent background.** Always pair `backdrop-blur-glass` with at least `bg-bg-glass` — otherwise blur has no effect.
- **z-index stacking.** Sidebar = `z-20`, mobile overlay = `z-40`, mobile drawer = `z-50`, modals = `z-100`.
- **No SVG sprites or external fonts.** Icons come only from `lucide-react`.
- **SPA fallback.** FastAPI serves `static/spa/index.html` for all non-API routes. Never add a `<base href>` — Vite's `base: '/static/spa/'` handles asset paths in production.
- **Build alias.** `@/` resolves to `frontend/src/` — use it everywhere, never relative `../../`.
- **ai-elements devDep.** The `ai-elements` npm package is in `devDependencies`. At runtime the SPA uses only the vendored source in `src/components/ai-elements/`. This is intentional — no CDN dependency.
