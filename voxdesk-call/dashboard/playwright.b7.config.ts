import fs from 'node:fs';
import { defineConfig } from '@playwright/test';

const systemChromium = ['/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/google-chrome'].find(
  (candidate) => fs.existsSync(candidate),
);

/** Live API on :4173 — no vite preview webServer. */
export default defineConfig({
  testDir: './e2e',
  testMatch: 'login-reload-agents.spec.ts',
  timeout: 60_000,
  fullyParallel: false,
  retries: 0,
  reporter: [['list'], ['json', { outputFile: 'test-results/b7-login-reload.json' }]],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://127.0.0.1:4173',
    headless: true,
    launchOptions: {
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || systemChromium,
      args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
    },
  },
});
