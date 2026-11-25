// Authenticated browser journey, memory-lean. PART 17B follow-up.
//
// The Playwright suite (`e2e/journeys.spec.ts`) needs the same six processes this
// does, and on a 2 GB box the OOM killer took the API while Chromium was starting
// (`anon-rss:719880kB` for uvicorn, `dmesg` clear about it). So this script does
// the one journey that matters most — a real operator signing in through the real
// form, landing on the real console — with Chromium restricted to a size the box
// can hold:
//
//   --single-process, no GPU, no images, 256 MB JS heap cap.
//
// Everything it asserts is what a person would see: the form, the POST /auth/login
// status, the console URL, the token the app stores, and an axe scan of the page
// they land on.
//
// Usage:  node browser_journey.mjs http://127.0.0.1:8080 EMAIL PASSWORD

import { chromium } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const [base = "http://127.0.0.1:8080", email = "", password = ""] = process.argv.slice(2);
if (!email || !password) {
  console.error("usage: node browser_journey.mjs <base> <email> <password>");
  process.exit(64);
}

const out = { base, steps: [] };
const browser = await chromium.launch({
  headless: true,
  args: [
    "--single-process",
    "--no-zygote",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--disable-extensions",
    "--blink-settings=imagesEnabled=false",
    "--js-flags=--max-old-space-size=256",
  ],
});

try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text().slice(0, 200)));

  const login = await page.goto(`${base}/login`, { waitUntil: "domcontentloaded", timeout: 90_000 });
  out.steps.push({ step: "open /login", status: login?.status() ?? 0 });

  await page.getByLabel(/email/i).fill(email);
  await page.getByLabel(/password/i).fill(password);
  out.steps.push({ step: "fill form", ok: true });

  const waitLogin = page.waitForResponse(
    (r) => r.url().includes("/auth/login") && r.request().method() === "POST",
    { timeout: 90_000 },
  );
  await page.getByRole("button", { name: /sign in|log in/i }).first().click();
  const response = await waitLogin;
  out.steps.push({ step: "POST /auth/login", status: response.status() });

  await page.waitForURL(/\/dashboard/, { timeout: 90_000 });
  out.steps.push({ step: "land on console", url: new URL(page.url()).pathname });

  const token = await page.evaluate(() => window.sessionStorage.getItem("voxdesk.access_token"));
  out.tokenStored = Boolean(token);

  const heading = await page.locator("h1, h2, main").first().isVisible().catch(() => false);
  out.consoleHeadingVisible = heading;

  const axe = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  out.axe = {
    violations: axe.violations.length,
    byImpact: axe.violations.reduce((acc, v) => ({ ...acc, [v.impact]: (acc[v.impact] ?? 0) + 1 }), {}),
    ids: axe.violations.map((v) => `${v.id} (${v.impact})`),
  };
  out.consoleErrors = consoleErrors.slice(0, 5);
  await context.close();
} finally {
  await browser.close();
}

console.log(JSON.stringify(out, null, 2));
const loginStep = out.steps.find((s) => s.step === "POST /auth/login");
const failed = !loginStep || loginStep.status !== 200 || !out.tokenStored;
process.exit(failed ? 1 : 0);
