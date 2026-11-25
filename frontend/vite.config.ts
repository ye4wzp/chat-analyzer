import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // Match CA_PORT used by scripts/start.command when the default port is taken.
      '/api': process.env.CA_API || `http://127.0.0.1:${process.env.CA_PORT || 8000}`,
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.match(/node_modules\/(react|react-dom|react-router|react-router-dom|scheduler)\//)) return 'react'
          if (id.match(/node_modules\/(recharts|d3-[a-z]+|victory-vendor|internmap|decimal\.js-light)\//)) return 'charts'
        },
      },
    },
  },
})
