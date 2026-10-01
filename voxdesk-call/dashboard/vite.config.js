import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// RISK-01 Fix: allowedHosts true is broad. Restrict to opt-in.
// - Default: host check enabled (secure)
// - VOXDESK_ALLOW_ALL_HOSTS=true or NODE_ENV!=production allows all (for preview/tunnel)
// - See docs/SECURITY.md and audit RISK-01

const allowAllHosts = process.env.VOXDESK_ALLOW_ALL_HOSTS === 'true' || process.env.VITE_ALLOW_ALL_HOSTS === 'true'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    // Vite 6 rejects unknown Host headers. Local dev is unaffected; this only
    // lets the app be reached through a hosted preview/tunnel domain.
    // FIX: Restrict hosts in shared/tunneled environments; keep broad mode opt-in only per RISK-01
    // When VOXDESK_ALLOW_ALL_HOSTS=true, allow all (for Arena preview, tunnels)
    // Otherwise, use default host check (secure)
    allowedHosts: allowAllHosts ? true : undefined,
    // Browser calls /api -> vite proxies to FastAPI. Never hardcode localhost in the browser.
    // /auth is proxied too, so the HttpOnly refresh cookie stays same-origin.
    // /realtime/ws upgrades to the websocket gateway — the entry mirrors the
    // production Caddy rule (path rewritten to /ws) so dev and prod see the
    // same wire from the client's seat.
    proxy: {
      '/api': 'http://localhost:8000',
      '/auth': 'http://localhost:8000',
      '/realtime/ws': {
        target: 'http://localhost:8790',
        ws: true,
        rewrite: (path) => path.replace(/^\/realtime\/ws/, '/ws'),
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/setup.js'],
    include: ['tests/**/*.test.{js,jsx}', 'src/tests/**/*.test.{ts,tsx,js,jsx}'],
    // The XSS-boundary test greps the real source tree, so it must not be
    // confused by a build directory.
    exclude: ['node_modules', 'dist'],
    restoreMocks: true,
  },
})
