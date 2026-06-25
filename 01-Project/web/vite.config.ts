import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Built bundle goes to the FastAPI server's static dir, which it serves at "/".
// In dev, /ws and /api are proxied to the Python backend on :8000.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: '/',
  build: { outDir: '../market_sim/server/static', emptyOutDir: true },
  server: {
    port: 5173,
    proxy: {
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
