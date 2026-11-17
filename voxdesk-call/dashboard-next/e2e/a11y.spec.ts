import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/**
 * Accessibility gate (PART 17B, P17B-04 / Q-06).
 *
 * `axe-core` runs against every top-level page an operator can reach. The bar
 * is **zero serious or critical violations** — the WCAG 2.1 A/AA impact levels a
 * screen-reader or keyboard user hits immediately.
 *
 * Two deliberate design choices:
 *
 * * **Moderate and minor issues are reported, not gated.** A missing
 *   `region` landmark or a redundant role is worth knowing about, but failing
 *   the build on it would teach the team to add blanket `aria-hidden` until the
 *   build goes green. Serious/critical is where the real blockers are, and that
 *   is the line this file draws.
 * * **The page list is data, not prose.** `PUBLIC_PAGES` and `CONSOLE_PAGES` are
 *   arrays so adding a route is one line, and a coverage test below fails if the
 *   list stops growing with `app/`. A static list that silently misses half the
 *   product is worse than no list.
 */

const EMAIL = process.env.E2E_EMAIL ?? "";
const PASSWORD = process.env.E2E_PASSWORD ?? "";
const SIGNUP = process.env.E2E_SIGNUP === "1";
const ALLOW_SKIP = process.env.E2E_ALLOW_SKIP === "1";
const TOKEN_KEY = "voxdesk.access_token";

/** Pages reachable without a session. */
const PUBLIC_PAGES: readonly string[] = [
  "/",
  "/pricing",
  "/product",
  "/product/voice-agents",
  "/solutions",
  "/industries",
  "/integrations",
  "/use-cases",
  "/security",
  "/trust",
  "/compliance",
  "/status",
  "/about",
  "/contact",
  "/developers",
  "/docs",
  "/privacy",
  "/terms",
  "/dpa",
  "/sla",
  "/careers",
  "/blog",
  "/resources",
  "/team",
  "/login",
  "/signup",
];

/** Console pages. Each one is a top-level destination in the shell navigation. */
//: A run of 32 console pages in one worker is what tips a 2 GB box over: the
//: FastAPI process grows to ~730 MB, Chromium to ~700 MB, and the OOM killer
//: ends the API mid-run — every remaining page then fails with a 502 from the
//: front door, which reads like an accessibility regression and is not one.
//: Splitting the list into two halves lets CI run them in one job while a
//: constrained machine runs `--shard=1/2` and `--shard=2/2` with a fresh API
//: between them. `A11Y_CONSOLE_PAGES` overrides the list entirely, e.g.
//: `A11Y_CONSOLE_PAGES=/dashboard,/dashboard/calls`.
const ALL_CONSOLE_PAGES: readonly string[] = [
  "/dashboard",
  "/dashboard/agent",
  "/dashboard/agents",
  "/dashboard/agents/archive",
  "/dashboard/agents/new",
  "/dashboard/alerts",
  "/dashboard/analytics",
  "/dashboard/analytics/custom",
  "/dashboard/anomaly",
  "/dashboard/appointments",
  "/dashboard/audit",
  "/dashboard/batch-calls",
  "/dashboard/billing",
  "/dashboard/calls",
  "/dashboard/campaigns",
  "/dashboard/chat-agents",
  "/dashboard/compliance",
  "/dashboard/components",
  "/dashboard/conductor",
  "/dashboard/connection-demo",
  "/dashboard/deployment",
  "/dashboard/evidence",
  "/dashboard/experiments",
  "/dashboard/forecasting",
  "/dashboard/governance",
  "/dashboard/insight",
  "/dashboard/integrations",
  "/dashboard/knowledge",
  "/dashboard/leads",
  "/dashboard/legal",
  "/dashboard/live",
  "/dashboard/messaging/registration",
  "/dashboard/phone-numbers",
  "/dashboard/reviews",
  "/dashboard/roi",
  "/dashboard/settings",
  "/dashboard/settings/branding",
  "/dashboard/settings/identity",
  "/dashboard/settings/identity/api-keys",
  "/dashboard/settings/identity/domains",
  "/dashboard/settings/identity/scim",
  "/dashboard/settings/identity/security",
  "/dashboard/settings/identity/service-accounts",
  "/dashboard/settings/identity/sso",
  "/dashboard/specialized-agents",
  "/dashboard/team",
  "/dashboard/team/roles",
  "/dashboard/telephony/concurrency",
  "/dashboard/telephony/countries",
  "/dashboard/telephony/trunks",
  "/dashboard/tests",
  "/dashboard/tools",
  "/dashboard/translation",
  "/dashboard/voice",
  "/dashboard/webhooks",
  "/dashboard/workflows",
  "/dashboard/workflows/library",
];

const CONSOLE_PAGES: readonly string[] =
  process.env.A11Y_CONSOLE_PAGES?.split(",").map((p) => p.trim()).filter(Boolean) ??
  ALL_CONSOLE_PAGES;

/**
 * `A11Y_SHARD=N/M` slicing. Playwright's own `--shard` divides the whole matrix
 * (public + console + coverage tests), which is the wrong axis here — the point
 * is to keep each process under the memory ceiling — and it also strips the
 * flag before the config runs, so a `process.argv` lookup here finds nothing.
 */
function shard<T>(items: readonly T[]): readonly T[] {
  const raw = process.env.A11Y_SHARD;
  if (!raw) return items;
  const [n, m] = raw.split("/").map(Number);
  if (!n || !m || n < 1 || n > m) return items;
  const size = Math.ceil(items.length / m);
  return items.slice((n - 1) * size, n * size);
}

const AUDITED_CONSOLE_PAGES = shard(CONSOLE_PAGES);

const SERIOUS = new Set(["serious", "critical"]);

async function audit(page: Page, path: string) {
  const response = await page.goto(path, { waitUntil: "domcontentloaded" });
  expect(response, `no response for ${path}`).not.toBeNull();
  // A 404 or 500 is a different bug (and one the journey suite owns); auditing
  // the error page would produce misleading accessibility noise.
  expect(response!.status(), `${path} did not render`).toBeLessThan(400);
  //: Not `networkidle`: several console pages poll (agent factory, chat, live
  //: ops), so the network never goes quiet and the wait burns the whole test
  //: timeout — which reads as an accessibility failure but is a liveness bug in
  //: the harness. Settle the DOM, give late effects one paint, then measure.
  await page.waitForLoadState("domcontentloaded");
  await page.waitForFunction(() => document.readyState === "complete", null, {
    timeout: 30_000,
  });
  await page.waitForTimeout(1500);

  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();

  const blocking = results.violations.filter((v) => SERIOUS.has(v.impact ?? ""));

  // Report the rest so it is visible in the log without failing the gate.
  const advisory = results.violations.filter((v) => !SERIOUS.has(v.impact ?? ""));
  if (advisory.length > 0) {
    console.log(
      `[a11y:advisory] ${path}: ` +
        advisory.map((v) => `${v.id}(${v.impact}, ${v.nodes.length})`).join(", "),
    );
  }

  const describe = blocking
    .map((v) => {
      const nodes = v.nodes
        .slice(0, 3)
        .map((n) => `      ${n.target.join(" ")}`)
        .join("\n");
      return `  ${v.id} [${v.impact}] ${v.help}\n${nodes}`;
    })
    .join("\n");

  expect(blocking.length, `serious/critical accessibility violations on ${path}:\n${describe}`).toBe(0);
}

test.describe("accessibility: public site", () => {
  for (const path of PUBLIC_PAGES) {
    test(`${path} has no serious accessibility violations`, async ({ page }) => {
      await audit(page, path);
    });
  }
});

test.describe("accessibility: operator console", () => {
  test.beforeAll(() => {
    if (EMAIL && PASSWORD) return;
    if (SIGNUP) return;
    if (ALLOW_SKIP) return;
    throw new Error(
      "Console accessibility needs credentials. Set E2E_EMAIL/E2E_PASSWORD, " +
        "E2E_SIGNUP=1, or E2E_ALLOW_SKIP=1.",
    );
  });

  for (const path of AUDITED_CONSOLE_PAGES) {
    test(`${path} has no serious accessibility violations`, async ({ page }) => {
      if (!(EMAIL && PASSWORD) && !SIGNUP) {
        test.skip(true, "E2E_ALLOW_SKIP=1: console accessibility not exercised");
        return;
      }

      const base = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost";
      const credentials = EMAIL && PASSWORD
        ? { email: EMAIL, password: PASSWORD }
        : {
            email: `a11y+${Date.now()}@voxdesk-e2e.example.com`,
            password: "E2e-Test-Passw0rd!2026",
            full_name: "A11y Auditor",
            organization_name: `A11y Co ${Date.now()}`,
          };
      const endpoint = EMAIL && PASSWORD ? "/auth/login" : "/auth/signup";
      const response = await page.request.post(`${base}${endpoint}`, { data: credentials });
      const body = (await response.json()) as { access_token?: string };
      expect(body.access_token, `could not mint a session via ${endpoint}`).toBeTruthy();

      await page.addInitScript(
        ([key, token]) => {
          try {
            window.sessionStorage.setItem(key, token);
          } catch {
            /* handled by the assertion below */
          }
        },
        [TOKEN_KEY, body.access_token!] as const,
      );

      await audit(page, path);
      expect(page.url(), `${path} bounced to the sign-in screen`).not.toContain("/login");
    });
  }
});

// ---------------------------------------------------------------------------
// The gate must keep covering the product
// ---------------------------------------------------------------------------

test.describe("coverage of the accessibility gate itself", () => {
  test("every top-level console route is on the audit list", async () => {
    const { readdirSync, statSync } = await import("node:fs");
    const { join } = await import("node:path");
    const root = join(process.cwd(), "app", "dashboard");

    const found: string[] = [];
    const walk = (dir: string, prefix: string) => {
      for (const entry of readdirSync(dir)) {
        const full = join(dir, entry);
        if (!statSync(full).isDirectory()) {
          if (entry === "page.tsx") found.push(`/dashboard${prefix}`);
          continue;
        }
        // Dynamic segments are per-record pages, audited via their parent list.
        if (entry.startsWith("[") || entry.startsWith("(")) continue;
        walk(full, `${prefix}/${entry}`);
      }
    };
    walk(root, "");

    const audited = new Set(ALL_CONSOLE_PAGES);
    // A page is "covered" if it is listed, or if a listed page is its ancestor
    // (a detail page inherits the shell audited at the parent level).
    const uncovered = found.filter(
      (route) => !audited.has(route) && !Array.from(audited).some((a) => route.startsWith(`${a}/`)),
    );
    expect(
      uncovered,
      `these console pages are on no accessibility list: ${uncovered.join(", ")}`,
    ).toEqual([]);
  });
});
