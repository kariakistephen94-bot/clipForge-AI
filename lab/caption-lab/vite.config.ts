import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Isolated Caption Lab. Shares the dashboard's installed packages (node_modules is a symlink to
// ../../frontend/node_modules) but has its own entry point, port and build output. Not part of the app.
export default defineConfig({
  plugins: [react()],
  server: { port: 5180, strictPort: true },
  build: { outDir: 'dist' },
})
