// File: dashboard-next/app/dashboard/agents/__tests__/agents_create.test.tsx — pins the create-agent contract found by the PART 17A shipped-image Playwright journey

import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AgentsListPage from "@/app/dashboard/agents/page";

/**
 * These tests exist because the shipped image, not the type checker, found the
 * bug they pin.
 *
 * `POST /api/agents` accepts `AgentConfigPayload`, which is `extra="forbid"`.
 * The page used to send `initial_config: { mode }` — a field the route does not
 * declare — so every "+ New Agent" click answered 422
 * ("Extra inputs are not permitted") and no agent was created. TypeScript was
 * happy: the *client* had declared the field itself.
 *
 *   1. the create body carries only declared fields;
 *   2. engine mode is applied through the draft flow endpoint, where `mode`
 *      actually lives;
 *   3. a flow-mode failure is reported but does not deny a real creation.
 */

interface Call {
  path: string;
  init?: RequestInit;
}

const state = vi.hoisted(() => {
  const calls: Call[] = [];
  return {
    calls,
    failFlow: false,
    agents: [] as Array<Record<string, unknown>>,
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

  const api = {
    listAgents: vi.fn(async () => state.agents),
    createAgent: vi.fn(async (payload: Record<string, unknown>) => {
      state.calls.push({ path: "/api/agents", init: { method: "POST", body: JSON.stringify(payload) } });
      const created = {
        id: "3f2b1c9e-0000-4000-8000-abcdefabcdef",
        name: payload.name,
        status: "draft",
      };
      state.agents = [created];
      return created;
    }),
    getAgentFlow: vi.fn(async (agentId: string) => {
      state.calls.push({ path: `/api/agents/${agentId}/flow`, init: { method: "GET" } });
      if (state.failFlow) throw new Error("flow endpoint unavailable");
      return { mode: "single_prompt", flow: { nodes: [{ id: "n1" }], edges: [] } };
    }),
    putAgentFlow: vi.fn(async (agentId: string, payload: Record<string, unknown>) => {
      state.calls.push({
        path: `/api/agents/${agentId}/flow`,
        init: { method: "PUT", body: JSON.stringify(payload) },
      });
      if (state.failFlow) throw new Error("flow endpoint unavailable");
      return { mode: payload.mode };
    }),
    duplicateAgent: vi.fn(async () => ({})),
    archiveAgent: vi.fn(async () => ({})),
  };

  return { ApiError, api, default: api };
});

const roots: Array<ReturnType<typeof createRoot>> = [];

async function render() {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const host = document.createElement("div");
  const root = createRoot(host);
  roots.push(root);
  await act(async () => {
    root.render(<AgentsListPage />);
  });
  await act(async () => {});
  return host;
}

function field(host: HTMLElement, placeholder: string): HTMLInputElement {
  const input = [...host.querySelectorAll("input")].find(
    (candidate) => candidate.getAttribute("placeholder") === placeholder,
  );
  if (!input) throw new Error(`no input with placeholder ${placeholder}`);
  return input as HTMLInputElement;
}

/** Set a controlled input's value so React's onChange fires. */
async function type(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  await act(async () => {
    setter?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function submit(host: HTMLElement) {
  const form = host.querySelector('[data-testid="create-agent-form"]');
  if (!form) throw new Error("no create-agent form");
  await act(async () => {
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  await act(async () => {});
}

beforeEach(() => {
  state.calls.length = 0;
  state.failFlow = false;
  state.agents = [];
});

afterEach(async () => {
  for (const root of roots.splice(0)) {
    await act(async () => root.unmount());
  }
  vi.unstubAllGlobals();
});

describe("create agent", () => {
  it("sends only fields POST /api/agents declares", async () => {
    const host = await render();
    await type(field(host, "e.g. Inbound Sales Qualifier"), "E2E agent 1");
    await submit(host);

    const create = state.calls.find(
      (call) => call.path === "/api/agents" && call.init?.method === "POST",
    );
    expect(create, "no POST /api/agents was made").toBeDefined();

    const body = JSON.parse(String(create?.init?.body)) as Record<string, unknown>;
    // The route is extra="forbid" — an undeclared key is a 422, not a no-op.
    expect(Object.keys(body).sort()).toEqual(["description", "name"]);
    expect(body.name).toBe("E2E agent 1");
    expect(host.textContent).toContain('Created agent "E2E agent 1"');
  });

  it("applies engine mode through the draft flow endpoint", async () => {
    const host = await render();
    await type(field(host, "e.g. Inbound Sales Qualifier"), "E2E agent 2");
    await submit(host);

    const put = state.calls.find(
      (call) => call.path.endsWith("/flow") && call.init?.method === "PUT",
    );
    expect(put, "engine mode was never sent to the flow endpoint").toBeDefined();
    const body = JSON.parse(String(put?.init?.body)) as Record<string, unknown>;
    expect(body.mode).toBe("flow");
    // The existing flow graph is carried over, not replaced with an empty one.
    expect(body.flow).toEqual({ nodes: [{ id: "n1" }], edges: [] });
  });

  it("reports a flow-mode failure without pretending the agent was not created", async () => {
    state.failFlow = true;
    const host = await render();
    await type(field(host, "e.g. Inbound Sales Qualifier"), "E2E agent 3");
    await submit(host);

    expect(host.textContent).toContain('Created agent "E2E agent 3"');
    expect(host.textContent).toContain("Flow mode could not be applied");
    expect(host.querySelector('[role="alert"]')).toBeNull();
  });
});
