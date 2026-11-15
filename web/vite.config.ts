import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: {
    // npm run dev talks to a running `stage serve` on its default port.
    proxy: { '/api': 'http://127.0.0.1:8791' },
  },
  // `vite build --mode public` builds only the phone's page (public.html) for
  // Vercel (docs/PLAN.md section 5.9); the default build is the Stage's own.
  build:
    mode === 'public'
      ? { outDir: 'dist-public', rollupOptions: { input: 'public.html' } }
      : undefined,
}))
