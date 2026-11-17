import { expect, test, type Page } from "@playwright/test";

/**
 * Shipped-image smoke (PART 17A, P17A-06).
 *
 * What this asserts, and why it is split in two:
 *
 * 1. **The image serves the Next build.** No credentials needed. If this group
 *    fails, the image is wrong — and that is true for every deployment, so it
 *    must never be skippable.
 *
 * 2. **The operator journey works**: sign in, create an agent, open its flow
 *    builder, confirm the live monitor loads, and open the test-call page. This
 *    group needs a real account in a real database, so it reads
 *    `E2E_EMAIL`/`E2E_PASSWORD`. Missing credentials are a *failure with an
 *    explanation*, not a skip: a journey that silently skips every time is a
 *    journey nobody is testing. `E2E_ALLOW_SKIP=1` exists only for the case
 *    where someone runs the whole suite on a laptop with no database, and it
 *    prints a loud SKIPPED line when it is used.
 *
 * Every assertion is about something a user would notice: a page that renders,
 * a control that exists, a console that shows a live connection state. Nothing
 * here asserts on internal markup that could change without changing behaviour.
 */

const BASE = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost";
const EMAIL = process.env.E2E_EMAIL ?? "";
const PASSWORD = process.env.E2E_PASSWORD ?? "";
const ALLOW_SKIP = process.env.E2E_ALLOW_SKIP === "1";

/** Wait for the document to be the Next app, not an error page or a proxy 502. */
async function expectNextDocument(page: Page, path: string) {
  const response = await page.goto(path, { waitUntil: "domcontentloaded" });
  expect(response, `no response for ${path}`).not.toBeNull();
  expect(response!.status(), `HTTP status for ${path}`).toBeLessThan(400);
  // A Next document references its hashed assets. An error page from Caddy or the
  // API does not, so this single check distinguishes "the frontend answered" from
  // "something else answered with 200".
  const html = await page.content();
  expect(html, `${path} was not served by the Next app`).toContain("/_next/static/");
  return response!;
}

test.describe("shipped image: the Next frontend is what is being served", () => {
  test("home page is the marketing site from dashboard-next", async ({ page }) => {
    await expectNextDocument(page, "/");
    // The root used to be a redirect to /login. It is the site now, and the site
    // has a sign-in link — if that link is missing, the console is unreachable
    // from the front door.
    const signIn = page.getByRole("link", { name: /sign in|log in|login/i }).first();
    await expect(signIn).toBeVisible();
  });

  test("CSP carries a per-request nonce and the inline bootstrap uses it", async ({ page }) => {
    const response = await page.goto("/", { waitUntil: "domcontentloaded" });
    const csp = response!.headers()["content-security-policy"] ?? "";
    expect(csp, "no Content-Security-Policy header").toContain("script-src");
    const nonce = /'nonce-([a-f0-9]+)'/.exec(csp)?.[1];
    expect(nonce, "the CSP has no nonce").toBeTruthy();
    // The very thing a nonce exists to allow: Next's inline bootstrap. If the
    // nonce is not on it, the browser blocks it and the page is inert.
    //
    // Read the *response body*, not `page.content()`: per the CSP spec the
    // `nonce` content attribute is emptied in the DOM so a script cannot read it
    // back (`document.querySelector('script').nonce` is the supported accessor),
    // so `page.content()` always reports `nonce=""` even when the policy is
    // correct. Asserting on the DOM here would fail a working page — which is
    // exactly what this test did before it was fixed.
    const html = await response!.text();
    expect(html).toContain(`nonce="${nonce}"`);
    expect(csp).toContain("'strict-dynamic'");
  });

  test("two requests get two different nonces", async ({ request }) => {
    const first = await request.get(`${BASE}/`);
    const second = await request.get(`${BASE}/`);
    const nonceOf = (value: string) => /'nonce-([a-f0-9]+)'/.exec(value)?.[1] ?? "";
    const a = nonceOf(first.headers()["content-security-policy"] ?? "");
    const b = nonceOf(second.headers()["content-security-policy"] ?? "");
    expect(a).toBeTruthy();
    expect(b).toBeTruthy();
    expect(a, "the nonce is not per-request").not.toBe(b);
  });

  test("login page renders a real form", async ({ page }) => {
    await expectNextDocument(page, "/login");
    await expect(page.locator('input[type="email"], input[name="email"]').first()).toBeVisible();
    await expect(page.locator('input[type="password"], input[name="password"]').first()).toBeVisible();
    await expect(page.getByRole("button", { name: /sign in|log in|continue/i }).first()).toBeVisible();
  });

  test("an unknown path is a 404, not a fake 200", async ({ page }) => {
    const response = await page.goto("/this-path-does-not-exist-either", {
      waitUntil: "domcontentloaded",
    });
    expect(response!.status()).toBe(404);
  });

  test("the API is same-origin: no absolute backend URL in the shipped bundle", async ({ page }) => {
    await expectNextDocument(page, "/");
    const body = (await page.content()).toLowerCase();
    // The old default was a hard-coded http://localhost:8000, which in a shipped
    // image points at the visitor's own machine. It must not come back.
    expect(body).not.toContain("localhost:8000");
  });
});

test.describe("shipped image: operator journey", () => {
  test("sign in, create an agent, open the builder, the live monitor, and test-call", async ({
    page,
  }) => {
    // Fail loud, skip only on request. A suite that silently skips the one test
    // that exercises the product is worse than one that goes red: CI would
    // report success while nothing was ever signed into.
    if (!EMAIL || !PASSWORD) {
      if (ALLOW_SKIP) {
        test.skip(true, "E2E_EMAIL/E2E_PASSWORD are not set and E2E_ALLOW_SKIP=1 — skipping by request.");
      }
      expect(
        EMAIL && PASSWORD,
        "E2E_EMAIL/E2E_PASSWORD are required: this journey is the only end-to-end " +
          "proof that sign-in, agent creation and the flow builder work against a " +
          "real API. Pass them, or set E2E_ALLOW_SKIP=1 to opt out explicitly.",
      ).toBeTruthy();
      return;
    }

    // ---- sign in -----------------------------------------------------------
    await expectNextDocument(page, "/login");
    // The inputs are controlled, so a fill that lands before React hydrates is
    // wiped back to the initial state — the classic symptom is an empty email
    // field and a native-validation block on submit. Wait for hydration, then
    // assert the values actually stuck before submitting.
    await page.waitForLoadState("networkidle");
    const emailInput = page.getByRole("textbox", { name: "Email" });
    const passwordInput = page.getByRole("textbox", { name: "Password" });
    await emailInput.fill(EMAIL);
    await passwordInput.fill(PASSWORD);
    await expect(emailInput).toHaveValue(EMAIL);
    await expect(passwordInput).toHaveValue(PASSWORD);
    await page.getByRole("button", { name: /sign in|log in|continue/i }).first().click();

    // The console shell, or a second-factor challenge if this account has MFA
    // enrolled. Both are legitimate; a stuck login page is not.
    await page.waitForURL(/(\/dashboard|\/mfa|\/verify)/, { timeout: 30_000 });
    test.skip(page.url().includes("/mfa"), "account requires MFA; run the journey with an MFA-free account");

    // ---- create an agent ---------------------------------------------------
    // Creation is inline on the list page — a form, not a separate /new route.
    // Fill the name, submit, and the new agent appears in the grid with a
    // backend-issued id in its links. That id is the proof the POST reached
    // the API: the client cannot invent it.
    await expectNextDocument(page, "/dashboard/agents");
    await page.waitForLoadState("networkidle");
    const name = `E2E agent ${Date.now()}`;
    const form = page.locator('[data-testid="create-agent-form"]');
    const nameInput = form.getByPlaceholder(/Inbound Sales Qualifier/);
    await nameInput.fill(name);
    await expect(nameInput).toHaveValue(name);
    await page.locator('[data-testid="create-agent-submit"]').click();

    // The page confirms creation with a status message naming the agent.
    await expect(page.getByRole("status")).toContainText(name, { timeout: 30_000 });

    // The agent's own links carry the backend-issued id.
    const detailLink = page.locator('[data-testid^="agent-detail-link-"]').first();
    await expect(detailLink).toBeVisible({ timeout: 30_000 });
    const agentId = (await detailLink.getAttribute("data-testid"))?.replace(
      "agent-detail-link-",
      "",
    );
    expect(agentId, "no agent id on the created agent's link").toBeTruthy();

    // ---- flow builder ------------------------------------------------------
    await expectNextDocument(page, `/dashboard/agents/${agentId}/flow`);
    // The builder paints a canvas or an empty-state; either way the page must
    // have produced something more than a spinner.
    await expect(page.locator("body")).not.toHaveText(/^\s*Loading…?\s*$/);

    // ---- live monitor ------------------------------------------------------
    await expectNextDocument(page, "/dashboard/live");
    await expect(page.locator("body")).not.toHaveText(/^\s*Loading…?\s*$/);

    // ---- test-call page ----------------------------------------------------
    await expectNextDocument(page, `/dashboard/agents/${agentId}/test-call`);
    await expect(page.getByRole("button", { name: /start|call|begin/i }).first()).toBeVisible();
  });
});
