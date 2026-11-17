import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AlertsPage from "@/app/dashboard/alerts/page";

/**
 * The alerts page is the operator surface for PART 15's alert engine. These
 * tests pin the three claims the page makes about itself:
 *
 *   1. what it shows comes from `/api/alerts/*`, including the *suppressed*
 *      breaches, so a muted rule cannot look healthy;
 *   2. `Test` is a dry run — the request body says `deliver: false` and the line
 *      under the rule says `not recorded`;
 *   3. a failing API call is reported as an error, never rendered as "no rules".
 */

interface Call {
  path: string;
  init?: RequestInit;
}

const state = vi.hoisted(() => {
  const calls: Call[] = [];
  return {
    calls,
    failure: null as null | { status: number; message: string },
    rules: [
      {
        id: "11111111-1111-1111-1111-111111111111",
        name: "Error rate over 5%",
        metric: "error_rate",
        comparator: "gt",
        threshold: 5,
        window_minutes: 15,
        cooldown_minutes: 30,
        scope_kind: "tenant",
        scope_id: null,
        channels: ["in_app"],
        enabled: true,
        description: "error_rate is above 5 over 15 minutes",
        updated_at: "2026-11-08T09:00:00",
      },
    ],
    events: [
      {
        id: "aaaaaaaa-1111-1111-1111-111111111111",
        rule_id: "11111111-1111-1111-1111-111111111111",
        metric: "error_rate",
        observed_value: 12.5,
        threshold: 5,
        state: "suppressed_cooldown",
        deliveries: [],
        created_at: "2026-11-08T09:30:00",
      },
    ],
  };
});

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    code = "";
    reason = "";
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  }

  const request = vi.fn(async (path: string, init?: RequestInit) => {
    state.calls.push({ path, init });
    if (state.failure) throw new ApiError(state.failure.status, state.failure.message);
    if (path === "/api/alerts/rules") {
      return { rules: state.rules, metrics: ["error_rate"], comparators: ["gt", "lt"] };
    }
    if (path.startsWith("/api/alerts/events")) {
      return { events: state.events, hours: 48 };
    }
    if (path.endsWith("/test-fire")) {
      return {
        rule_id: state.rules[0].id,
        metric: "error_rate",
        observed: 12.5,
        threshold: 5,
        comparator: "gt",
        would_breach: true,
        sampled: true,
        recorded: false,
        delivered: [],
      };
    }
    if (path === "/api/alerts/evaluate?deliver=false") {
      return { evaluated: 1, breached: 1, deliver: false };
    }
    return {};
  });

  return { ApiError, request };
});

const roots: Array<ReturnType<typeof createRoot>> = [];

async function render() {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const host = document.createElement("div");
  const root = createRoot(host);
  roots.push(root);
  await act(async () => {
    root.render(<AlertsPage />);
  });
  await act(async () => {});
  return host;
}

function findButton(host: HTMLElement, label: string): HTMLButtonElement {
  const button = [...host.querySelectorAll("button")].find(
    (candidate) => candidate.textContent?.trim() === label,
  );
  if (!button) throw new Error(`no button labelled ${label}`);
  return button as HTMLButtonElement;
}

beforeEach(() => {
  state.calls.length = 0;
  state.failure = null;
});

afterEach(async () => {
  for (const root of roots.splice(0)) {
    await act(async () => root.unmount());
  }
  vi.unstubAllGlobals();
});

describe("alerts page", () => {
  it("shows rules and suppressed breaches read from the API", async () => {
    const host = await render();

    expect(state.calls.map((call) => call.path)).toContain("/api/alerts/rules");
    expect(host.textContent).toContain("Error rate over 5%");
    expect(host.textContent).toContain("error_rate is above 5 over 15 minutes");
    // A cooldown-suppressed breach is on screen; it is not hidden.
    expect(host.textContent).toContain("suppressed_cooldown");
    expect(host.textContent).toContain("12.5 (threshold 5)");
    expect(host.textContent).toContain("History (last 48 h)");
  });

  it("test-fire is a dry run and says so", async () => {
    const host = await render();

    await act(async () => {
      findButton(host, "Test").click();
    });

    const testCall = state.calls.find((call) => call.path.endsWith("/test-fire"));
    expect(testCall).toBeDefined();
    expect(testCall?.init?.method).toBe("POST");
    expect(JSON.parse(String(testCall?.init?.body))).toEqual({ deliver: false });
    expect(host.textContent).toContain("would fire");
    expect(host.textContent).toContain("not recorded");
  });

  it("deletes a rule with DELETE and reloads the list", async () => {
    const host = await render();

    await act(async () => {
      findButton(host, "Delete").click();
    });

    const deleteCall = state.calls.find((call) => call.init?.method === "DELETE");
    expect(deleteCall?.path).toBe(
      "/api/alerts/rules/11111111-1111-1111-1111-111111111111",
    );
    expect(
      state.calls.filter((call) => call.path === "/api/alerts/rules").length,
    ).toBeGreaterThanOrEqual(2);
  });

  it("reports a failing API instead of pretending there are no rules", async () => {
    state.failure = { status: 403, message: "analytics permission required" };
    const host = await render();

    const banner = host.querySelector('[data-testid="alerts-load-error"]');
    expect(banner?.textContent).toContain("analytics permission required");
    // An empty table on a failed load is the lie this page must not tell.
    expect(host.textContent).not.toContain("No rules yet");
    expect(host.textContent).not.toContain("Nothing breached in this window");
    expect(host.textContent).toContain("Unavailable — see the error above.");
    expect(host.textContent).not.toContain("Error rate over 5%");
  });

  it("evaluate-all asks the API for a dry run", async () => {
    const host = await render();

    await act(async () => {
      findButton(host, "Evaluate all now (dry run)").click();
    });

    const evaluate = state.calls.find((call) => call.path.startsWith("/api/alerts/evaluate"));
    expect(evaluate?.path).toBe("/api/alerts/evaluate?deliver=false");
    expect(host.textContent).toContain("Dry run — nothing was sent.");
  });
});
