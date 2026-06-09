import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

export default defineConfig({
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
  base: '/static/spa/',
  build: {
    outDir: '../static/spa',
    emptyOutDir: true,
  },
})
