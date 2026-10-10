import { test, expect } from '@playwright/test';

const PUBLIC_PATHS = [
  '/',
  '/pricing',
  '/docs',
  '/security',
  '/status',
  '/login',
  '/signup',
];

function stubPublicApiRoutes(page: import('@playwright/test').Page) {
  return page.route(/\/(?:api|auth)\//, async (route) => {
    const url = route.request().url();
    if (url.includes('/api/v1/public/site/pricing')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            id: 'starter',
            code: 'starter',
            name: 'Starter',
            catalogue_source: 'SEED_DEFAULT',
            description: 'Entry voice plan',
            monthly_price_cents: 4900,
            annual_price_cents: 47000,
            currency: 'USD',
            included_minutes: 500,
            included_sms_segments: 1000,
            included_llm_tokens: 500000,
            included_tts_characters: 250000,
            max_concurrency: 5,
            overage_enabled: true,
            trial_days: 14,
            features: ['Inbound & Outbound Voice'],
            cta_label: 'Start Free Trial',
            cta_href: '/signup',
            is_enterprise: false,
          },
        ]),
      });
      return;
    }
    if (url.includes('/api/v1/public/site/status') || url.includes('/api/v1/public/status')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          overall_status: 'operational',
          message: 'All checked public components responded.',
          checked_at: '2026-10-09T00:00:00Z',
          components: [
            {
              id: 'api',
              name: 'API Process',
              status: 'operational',
              description: 'HTTP health check responded.',
              updated_at: '2026-10-09T00:00:00Z',
            },
          ],
        }),
      });
      return;
    }
    if (url.includes('/api/v1/public/home')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          hero: { headline: 'Enterprise Voice AI', subheadline: 'Deterministic voice runtime' },
          capabilities: [],
          trust_badges: [],
        }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [], status: 'ok' }),
    });
  });
}

test.describe('Shipped Vite UI Browser Smoke (SELL CHECK 2 Step 7)', () => {
  test('1. Public routes render HTTP 200 with zero uncaught pageerror or console.error events', async ({
    page,
  }) => {
    await stubPublicApiRoutes(page);

    for (const routePath of PUBLIC_PATHS) {
      const pageErrors: string[] = [];
      const consoleErrors: string[] = [];

      const onPageError = (err: Error) => pageErrors.push(err.message);
      const onConsole = (msg: import('@playwright/test').ConsoleMessage) => {
        if (msg.type() === 'error') {
          consoleErrors.push(msg.text());
        }
      };

      page.on('pageerror', onPageError);
      page.on('console', onConsole);

      const response = await page.goto(routePath, { waitUntil: 'networkidle' });
      expect(response?.status(), `HTTP status for ${routePath}`).toBe(200);
      expect(pageErrors, `Uncaught pageerror on ${routePath}`).toEqual([]);
      expect(consoleErrors, `console.error on ${routePath}`).toEqual([]);

      const rootHtml = await page.locator('#root').innerHTML();
      expect(rootHtml.length, `Rendered DOM content for ${routePath}`).toBeGreaterThan(50);

      page.off('pageerror', onPageError);
      page.off('console', onConsole);
    }
  });

  test('2. Unauthenticated visit to /dashboard/agents enforces the login gate and preserves next=/dashboard/agents', async ({
    page,
  }) => {
    await stubPublicApiRoutes(page);
    await page.addInitScript(() => {
      window.localStorage.clear();
      window.sessionStorage.clear();
    });

    const response = await page.goto('/dashboard/agents', { waitUntil: 'networkidle' });
    expect(response?.status()).toBe(200);

    const bodyText = await page.locator('body').innerText();
    expect(bodyText).toMatch(/Sign in|Log in|Email|Password/i);
    expect(bodyText).toContain('/dashboard/agents');
  });

  test('3. No horizontal overflow on 375px mobile and 1440px desktop viewports on / and /pricing', async ({
    page,
  }) => {
    await stubPublicApiRoutes(page);

    const viewports = [
      { width: 375, height: 812, label: 'mobile-375' },
      { width: 1440, height: 900, label: 'desktop-1440' },
    ];

    for (const vp of viewports) {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      for (const routePath of ['/', '/pricing']) {
        await page.goto(routePath, { waitUntil: 'networkidle' });
        const dimensions = await page.evaluate(() => ({
          scrollWidth: document.documentElement.scrollWidth,
          innerWidth: window.innerWidth,
        }));
        expect(
          dimensions.scrollWidth,
          `Horizontal overflow on ${routePath} at ${vp.label} (${dimensions.scrollWidth} > ${dimensions.innerWidth})`,
        ).toBeLessThanOrEqual(dimensions.innerWidth);
      }
    }
  });
});
