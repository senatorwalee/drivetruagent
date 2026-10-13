import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// The web app calls the API under /api. In development Vite forwards those
// requests to the backend started with `make dev`, so the browser only ever
// talks to one origin and no CORS setup is needed.
export default defineConfig({
  plugins: [react()],
  server: {
    // Not Vite's usual 5173, which another project on this machine may hold.
    port: 5174,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    restoreMocks: true,
  },
})
