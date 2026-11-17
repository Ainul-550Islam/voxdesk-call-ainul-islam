import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright configuration for the shipped-image smoke (PART 17A, P17A-06).
 *
 * There is no `webServer` block on purpose. This suite tests the artefact that
 * actually ships — the `web` image behind Caddy — so it must not start a dev
 * server and quietly pass against something else. Bring the stack up first:
 *
 *   docker compose -f docker-compose.prod.yml up -d --build
 *   ( cd dashboard-next && npx playwright test e2e/shipped_image.spec.ts )
 *
 * `PLAYWRIGHT_BASE_URL` overrides the origin for a stack that is not on
 * `http://localhost` (a remote host, or a bare standalone server on a port).
 */
export default defineConfig({
  testDir: "./e2e",
  // A shipped-image smoke is a proof, not a load test: one worker, no retries.
  // A retry would turn a flaky first paint into a green run, which is the one
  // thing this file must never do.
  // One worker, no retries: a shipped-image journey is a proof, not a load test,
  // and a retry would turn a flaky first paint into a green run.
  //
  // The timeouts are sized for the *slowest* environment this runs in. The
  // verification sandbox has ~2 GB of RAM shared by the Next standalone server,
  // uvicorn, PostgreSQL, Redis and Chromium; a single console page there takes
  // 11-23 s to settle, and a 60 s budget fails under the accumulation of a full
  // 16-test run even though every page passes on its own. That is a property of
  // the machine, not of the product, so the budget is raised rather than the
  // assertions weakened. On CI hardware these run far inside the limit.
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 25_000 },
  reporter: [["list"]],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost",
    // Artefacts are kept only for failures; a passing run should leave the tree
    // clean enough that `git status` is readable.
    //
    // Tracing OFF by default, and this is measured rather than assumed: on a
    // 2 GB machine `retain-on-failure` screenshots every action, and the CDP
    // pipeline stalls under it. The symptom is nasty — `locator.count()` and
    // `allTextContents()` hang for 60 s while `page.evaluate()` in the same
    // moment returns instantly, which reads like an application freeze and is
    // not one (the page answers in ~2 s and stays responsive for 15 s in a raw
    // Chromium run, with zero long tasks). Set `PLAYWRIGHT_TRACE=retain-on-failure`
    // on a machine with memory to spare, or when chasing a real failure.
    trace: (process.env.PLAYWRIGHT_TRACE as "off" | "retain-on-failure" | "on" | undefined) ?? "off",
    screenshot: "only-on-failure",
    video: "off",
    ignoreHTTPSErrors: false,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
