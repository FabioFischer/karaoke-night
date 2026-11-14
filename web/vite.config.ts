import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // npm run dev talks to a running `stage serve` on its default port.
    proxy: { '/api': 'http://127.0.0.1:8791' },
  },
})
