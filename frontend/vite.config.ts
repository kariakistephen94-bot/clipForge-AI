import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev server proxies the API to the local FastAPI backend (the API key never reaches the browser).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8765', changeOrigin: false },
    },
  },
})
