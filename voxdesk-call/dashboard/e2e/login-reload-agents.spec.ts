import { test, expect } from '@playwright/test';

/**
 * B7 — real login → reload → Agents.
 *
 * Public-route smoke stubs /auth and /api, so it cannot prove T2/T3.
 * This spec talks to a live API (PLAYWRIGHT_E2E_EMAIL / PLAYWRIGHT_E2E_PASSWORD).
 * It does not skip when credentials are present; when they are absent the run
 * is marked skipped with the variable names, never as a pass.
 */
test.describe('B7 live login → reload → Agents', () => {
  test('login, reload, Agents carries a bearer and no token in Web Storage', async ({
    page,
  }) => {
    const email = process.env.PLAYWRIGHT_E2E_EMAIL || '';
    const password = process.env.PLAYWRIGHT_E2E_PASSWORD || '';
    test.skip(
      !email || !password,
      'needs PLAYWRIGHT_E2E_EMAIL and PLAYWRIGHT_E2E_PASSWORD against a live API',
    );

    const authHeaders: string[] = [];
    page.on('request', (request) => {
      const url = request.url();
      if (url.includes('/api/') && !url.includes('/api/v1/public/')) {
        authHeaders.push(request.headers()['authorization'] || '');
      }
    });

    await page.goto('/dashboard/agents', { waitUntil: 'load' });
    await expect(page.locator('body')).toContainText(/Sign in|Email|Password/i);

    await page.locator('input[type="email"]').fill(email);
    await page.locator('input[type="password"]').fill(password);
    await Promise.all([
      page.waitForResponse((response) => response.url().includes('/auth/login') && response.request().method() === 'POST', {
        timeout: 20_000,
      }),
      page.getByRole('button', { name: /sign in/i }).click(),
    ]);

    await expect(page.locator('body')).not.toContainText(/Sign in to your workspace/i);
    await expect(page.locator('body')).toContainText(/Voice|Chat Agents|All agents|Total Agents/i);

    await page.reload({ waitUntil: 'load' });
    await expect(page).toHaveURL(/\/dashboard\/agents/);
    await expect(page.locator('body')).not.toContainText(/Sign in to your workspace/i);
    await expect(page.locator('body')).toContainText(/Voice|Chat Agents|All agents|Total Agents/i);

    const stored = await page.evaluate(() => ({
      local: window.localStorage.getItem('voxdesk_access_token'),
      session: window.sessionStorage.getItem('voxdesk_access_token'),
    }));
    expect(stored.local, 'access token must not be in localStorage').toBeNull();
    expect(stored.session, 'access token must not be in sessionStorage').toBeNull();

    const bearer = authHeaders.filter((value) => value.startsWith('Bearer '));
    expect(
      bearer.length,
      'after reload, Agents API calls must send Authorization: Bearer …',
    ).toBeGreaterThan(0);
  });
});
