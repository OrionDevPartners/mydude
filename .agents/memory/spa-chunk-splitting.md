---
name: SPA frontend chunk splitting (Vite/rolldown)
description: How to safely code-split the React SPA's heavy chunks (streamdown markdown, livekit) without breaking the app.
---

# SPA chunk splitting (frontend/, Vite 8 + rolldown)

The two largest built chunks are the markdown/streamdown subtree (~476–488 kB) and
`livekit-client` (~506 kB). Lessons for keeping first load small AND the app working:

## Do NOT size-split tightly-coupled vendor ESM trees
Using `build.rolldownOptions.output.advancedChunks.groups[].maxSize` to subdivide the
streamdown/markdown graph (micromark/mdast/remark/rehype/hast/unist/...) produces a
**blank dashboard with NO console error** — module init order breaks (circular deps get
split across chunk boundaries, an export is read before its chunk initializes).
**Why:** chunk splitting is module-boundary based; arbitrary size cuts through a circular
graph yield undefined-access at eval time, which can silently unmount React.
**How to apply:** never `maxSize`-split markdown/streamdown (or similar interconnected
vendor graphs). Verify any chunking change by actually loading the dashboard e2e, not just
by checking the build warning is gone.

## Broad `test` regex grouping pulls the chunk onto the EAGER path
An `advancedChunks` group whose `test` regex matches shared utility packages
(`escape-string-regexp`, `devlop`, `estree-util-*`, `ccount`, etc.) forces the whole group
chunk to load eagerly, because eager code statically imports those utils → the group chunk
becomes a static dep of the entry (shows up as a `modulepreload` in built index.html).
**How to apply:** to keep a heavy subtree off first load, lazy-load its consumer instead of
force-grouping by regex; let default code-splitting emit the async chunk.

## The real first-load win: lazy-load streamdown's only consumer
streamdown is imported only by `ai-elements/message.tsx` (the `MessageResponse` component,
which is currently not rendered anywhere). It was pulled onto every page using ai-elements
(Dashboard/TaskDetail) just because `message.tsx` imported `Streamdown` at module top-level.
Fix: move the Streamdown renderer to its own module (`ai-elements/message-response.tsx`,
default export) and `React.lazy(() => import('./message-response'))` it in message.tsx
(Suspense fallback null). Then message.tsx no longer statically imports streamdown, so the
markdown chunk drops out of the eager graph entirely.

## livekit-client is irreducible
It ships as ONE pre-bundled ESM vendor module — cannot be subdivided at module boundaries.
It is already lazy-loaded (`await import('livekit-client')` in `AvatarCall.tsx`). The only
honest way to clear its >500 kB warning is `build.chunkSizeWarningLimit` (set to 520, with a
comment explaining why). Isolate it into a named `livekit` group for clarity.
