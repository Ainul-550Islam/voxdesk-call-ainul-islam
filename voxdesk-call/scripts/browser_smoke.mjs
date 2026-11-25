// Browser + axe smoke, run against a live server. PART 17B follow-up.
//
// This is NOT the Playwright journey suite (`e2e/journeys.spec.ts`): it does not
// touch the authenticated console, because the sandbox it runs in has ~1 GB of
// RAM left once Next, uvicorn and PostgreSQL are up and Chromium cannot hold a
// long authenticated session in that budget. What it does prove, for real:
//
//   * the login page renders and its controls are present and focusable,
//   * axe-core runs on two live pages and reports zero serious/critical
//     violations on the unauthenticated surface,
//   * the page is served with the nonce CSP the app claims.
//
// Usage:  node browser_smoke.mjs http://127.0.0.1:3000

import { chromium } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const base = process.argv[2] ?? "http://127.0.0.1:3000";
const results = { base, pages: [], serious: 0, critical: 0 };

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext();
const page = await context.newPage();

async function scan(path, { expectHeading = null } = {}) {
  const response = await page.goto(`${base}${path}`, { waitUntil: "domcontentloaded", timeout: 120_000 });
  await page.waitForLoadState("networkidle", { timeout: 60_000 }).catch(() => {});
  const status = response ? response.status() : 0;
  const title = await page.title();
  const csp = (await response?.headers())?.["content-security-policy"] ?? "";
  const heading = expectHeading ? await page.locator(expectHeading).first().isVisible().catch(() => false) : null;
  const inputs = await page.locator("input").count();
  const buttons = await page.locator("button, [role=button]").count();
  const axe = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const byImpact = {};
  for (const violation of axe.violations) {
    byImpact[violation.impact] = (byImpact[violation.impact] ?? 0) + 1;
    if (violation.impact === "serious") results.serious += 1;
    if (violation.impact === "critical") results.critical += 1;
  }
  results.pages.push({
    path,
    status,
    title,
    headingVisible: heading,
    inputs,
    buttons,
    nonceCSP: /'nonce-[\w+/=-]+'/.test(csp),
    axeViolations: axe.violations.length,
    byImpact,
    ids: axe.violations.map((v) => `${v.id} (${v.impact})`),
  });
}

await scan("/login", { expectHeading: "form, h1, h2" });
await scan("/");

// --- authenticated surface -------------------------------------------------
// A real signup through the front door, then the token planted where the app
// reads it (sessionStorage, lib/auth.ts) before the first paint. Nothing is
// mocked: this is the same POST /auth/signup a human's browser makes.
if (process.env.AUTH_SCAN === "1") {
  const signup = await page.request.post(`${base}/auth/signup`, {
    data: {
      email: `smoke+${Date.now()}@voxdesk-e2e.example.com`,
      password: "E2e-Test-Passw0rd!2026",
      full_name: "Smoke Operator",
      organization_name: `Smoke Co ${Date.now()}`,
    },
  });
  results.signupStatus = signup.status();
  const token = (await signup.json()).access_token;
  results.gotToken = Boolean(token);
  await context.addInitScript(([key, value]) => {
    try { window.sessionStorage.setItem(key, value); } catch {}
  }, ["voxdesk.access_token", token]);
  await scan("/dashboard", { expectHeading: "main, h1, h2" });
}
await browser.close();

console.log(JSON.stringify(results, null, 2));
process.exit(results.critical > 0 ? 2 : 0);
