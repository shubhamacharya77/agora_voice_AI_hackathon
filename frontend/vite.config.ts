import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
  // The Agora Web SDK (~1.6 MB) is its own chunk, loaded only when Agora voice is set up.
  build: { chunkSizeWarningLimit: 1700 },
})
