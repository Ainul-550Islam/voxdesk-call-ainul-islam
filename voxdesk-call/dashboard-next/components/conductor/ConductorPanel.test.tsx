import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ConductorPanel from "@/components/conductor/ConductorPanel";

/**
 * The conductor panel must not claim more than the backend did. These tests pin:
 *
 *   1. a proposal is shown as a proposal — validation state and the "mock
 *      provider" badge are visible, and nothing says "applied";
 *   2. `/apply` carries the base version and base hash the UI displayed, so a
 *      stale proposal fails on the server instead of silently rewriting the
 *      wrong version;
 *   3. after applying, the panel says whether production was published rather
 *      than implying it.
 */

interface Call {
  path: string;
  init?: RequestInit;
}

const PROPOSAL = {
  id: "22222222-2222-2222-2222-222222222222",
  session_id: "33333333-3333-3333-3333-333333333333",
  agent_id: "agent-1",
  agent_kind: "voice",
  base_version_number: 7,
  base_config_hash: "hash-7",
  request_text: "read back the appointment date",
  summary: "Confirm the appointment date out loud",
  rationale: "Callers rebook when the date is misheard.",
  status: "pending",
  validation_status: "valid",
  simulation_status: "not_run",
  risk_summary: { highest: "low" },
  resulting_version_number: null,
  is_mock_provider: true,
  provider: "mock",
  model: "mock-conductor",
  created_at: "2026-11-08T09:00:00",
};

const state = vi.hoisted(() => {
  const calls: Call[] = [];
  return { calls, applyPublishes: false };
});

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  }

  const request = vi.fn(async (path: string, init?: RequestInit) => {
    state.calls.push({ path, init });
    if (path.startsWith("/api/v1/conductor/context")) {
      return { agent_id: "agent-1", agent_kind: "voice", version_number: 7 };
    }
    if (path.startsWith("/api/v1/conductor/proposals?")) {
      return [PROPOSAL];
    }
    if (path.endsWith("/changes")) {
      return [
        {
          id: "44444444-4444-4444-4444-444444444444",
          sequence: 1,
          section: "prompt",
          path: "prompt.confirmations",
          operation: "set",
          reason: "Read the date back before booking.",
          risk_level: "low",
          validation_state: "valid",
          validation_messages: [],
          simulation_state: "not_run",
          approval_state: "pending",
        },
      ];
    }
    if (path.endsWith("/apply")) {
      return {
        proposal: { ...PROPOSAL, status: "applied", resulting_version_number: 8 },
        applied_change_ids: ["44444444-4444-4444-4444-444444444444"],
        skipped_change_ids: [],
        base_version_number: 7,
        resulting_version_number: 8,
        production_published: state.applyPublishes,
      };
    }
    return PROPOSAL;
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
    root.render(<ConductorPanel agentId="agent-1" />);
  });
  await act(async () => {});
  return host;
}

async function click(host: HTMLElement, label: string) {
  const button = [...host.querySelectorAll("button")].find(
    (candidate) => candidate.textContent?.trim() === label,
  );
  if (!button) throw new Error(`no button labelled ${label}`);
  await act(async () => {
    (button as HTMLButtonElement).click();
  });
  await act(async () => {});
}

beforeEach(() => {
  state.calls.length = 0;
  state.applyPublishes = false;
});

afterEach(async () => {
  for (const root of roots.splice(0)) {
    await act(async () => root.unmount());
  }
  vi.unstubAllGlobals();
});

describe("conductor panel", () => {
  it("renders the scoped context version and the proposal as a proposal", async () => {
    const host = await render();

    expect(state.calls.some((call) => call.path.includes("/api/v1/conductor/context"))).toBe(true);
    expect(host.textContent).toContain("Confirm the appointment date out loud");
    expect(host.textContent).toContain("valid");
    // The mock provider badge is shown; a mock model is never presented as real.
    expect(host.textContent).toContain("mock provider");
    expect(host.textContent).not.toContain("Applied 1 change");
  });

  it("shows the per-change risk and reason", async () => {
    const host = await render();

    await click(host, "Inspect");

    expect(host.textContent).toContain("prompt.confirmations");
    expect(host.textContent).toContain("Read the date back before booking.");
    expect(host.textContent).toContain("low");
    expect(state.calls.some((call) => call.path.endsWith("/changes"))).toBe(true);
  });

  it("applies against the version and hash it displayed and states publication truthfully", async () => {
    const host = await render();

    await click(host, "Inspect");
    await click(host, "Apply to new version");

    const apply = state.calls.find((call) => call.path.endsWith("/apply"));
    expect(apply?.init?.method).toBe("POST");
    const body = JSON.parse(String(apply?.init?.body));
    expect(body.expected_base_version_number).toBe(7);
    expect(body.expected_base_config_hash).toBe("hash-7");
    expect(host.textContent).toContain("Applied 1 change(s) into version 8 (from 7).");
    expect(host.textContent).toContain("Not published — a human still has to publish.");
  });

  it("does not claim production publication unless the server says so", async () => {
    state.applyPublishes = true;
    const host = await render();

    await click(host, "Inspect");
    await click(host, "Apply to new version");

    expect(host.textContent).toContain("Production published.");
    expect(host.textContent).not.toContain("Not published");
  });
});
