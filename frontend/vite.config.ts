import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

export default defineConfig(({ command }) => ({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
      '@streamdown/mermaid': path.resolve(__dirname, './src/lib/streamdown-plugins-noop.ts'),
      '@streamdown/code': path.resolve(__dirname, './src/lib/streamdown-plugins-noop.ts'),
      '@streamdown/math': path.resolve(__dirname, './src/lib/streamdown-plugins-noop.ts'),
      '@streamdown/cjk': path.resolve(__dirname, './src/lib/streamdown-plugins-noop.ts'),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    allowedHosts: true,
    proxy: {
      '/api': {
        target: 'http://localhost:5000',
        changeOrigin: true,
      },
    },
  },
  base: command === 'serve' ? '/' : '/static/spa/',
  build: {
    outDir: '../static/spa',
    emptyOutDir: true,
    // The markdown/streamdown subtree (~488 kB) is kept off the initial load by
    // lazy-loading its only consumer (MessageResponse in ai-elements/message.tsx);
    // default code-splitting then emits it as an on-demand chunk.
    // livekit-client ships as ONE pre-bundled ESM vendor module (~506 kB) that
    // cannot be subdivided at module boundaries; it is already lazy-loaded only
    // on the Avatar page, so we isolate it into its own named chunk and raise the
    // warning limit just enough to cover that single irreducible vendor module.
    chunkSizeWarningLimit: 520,
    rolldownOptions: {
      output: {
        advancedChunks: {
          groups: [
            {
              name: 'livekit',
              test: /[\\/]node_modules[\\/]livekit-client[\\/]/,
            },
          ],
        },
      },
    },
  },
}))
