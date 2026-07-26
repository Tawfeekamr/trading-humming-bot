import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev proxy: the app calls /api/v1/trades same-origin; Vite forwards to the
// bot's :3030 API reached via the SSM port-forward tunnel (localhost:3030).
// When the tunnel is down, the app falls back to mock data (see src/lib/).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:3030', changeOrigin: false },
    },
  },
})
