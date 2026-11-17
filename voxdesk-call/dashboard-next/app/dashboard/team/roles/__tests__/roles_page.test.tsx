import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import CustomRolesPage from "@/app/dashboard/team/roles/page";

/**
 * The custom-role editor (PART 16, P16-01). These tests pin the claims the page
 * makes about itself, because a role editor is the screen where a cheerful
 * default is a security bug:
 *
 *   1. what it shows comes from `/api/auth/*`, grouped by module;
 *   2. a permission the caller does not hold is **visible but disabled** — the
 *      server's `grantable` list decides, not the browser;
 *   3. saving sends exactly the ticked permissions, and an escalation refusal
 *      (403 `escalation_refused`) is shown verbatim instead of being swallowed;
 *   4. a failed load is an error, never "no custom roles yet".
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
    saveFailure: null as null | { status: number; message: string },
    roles: [
      {
        id: "22222222-2222-2222-2222-222222222222",
        name: "QA reviewer",
        description: "Read QA scores",
        permissions: ["qa:read", "qa:write"],
        modules: { qa: 2 },
        is_active: true,
        updated_at: "2026-11-08T09:00:00",
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
    if (init?.method && init.method !== "GET" && state.saveFailure) {
      throw new ApiError(state.saveFailure.status, state.saveFailure.message);
    }
    if (state.failure) throw new ApiError(state.failure.status, state.failure.message);
    if (path === "/api/auth/permission-catalogue") {
      return {
        modules: [
          {
            module: "qa",
            label: "Quality assurance",
            permissions: ["qa:read", "qa:write"],
            grantable: ["qa:read"],
          },
          {
            module: "billing",
            label: "Billing",
            permissions: ["billing:write"],
            grantable: [],
          },
        ],
        total_permissions: 3,
        ungrouped: [],
      };
    }
    if (path === "/api/auth/custom-roles") {
      return { roles: state.roles, subject_types: ["user", "service_account", "scim_group"], max_roles: 50 };
    }
    if (path.endsWith("/assignments")) {
      return {
        role_id: state.roles[0].id,
        assignments: [
          {
            id: "33333333-3333-3333-3333-333333333333",
            subject_type: "user",
            subject_id: "44444444-4444-4444-4444-444444444444",
            created_at: "2026-11-08T10:00:00",
          },
        ],
      };
    }
    if (path.startsWith("/api/auth/effective-permissions")) {
      return {
        user_id: "44444444-4444-4444-4444-444444444444",
        email: "someone@example.com",
        builtin_role: "viewer",
        builtin_permissions: ["analytics:read"],
        custom_roles: [{ id: state.roles[0].id, name: "QA reviewer" }],
        custom_permissions: ["qa:read", "qa:write"],
        effective_permissions: ["analytics:read", "qa:read", "qa:write"],
      };
    }
    if (init?.method === "POST" && path === "/api/auth/custom-roles") {
      return { ...state.roles[0], id: "55555555-5555-5555-5555-555555555555", name: "Night shift" };
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
    root.render(<CustomRolesPage />);
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

function checkboxFor(host: HTMLElement, permission: string): HTMLInputElement {
  const code = [...host.querySelectorAll("code")].find((node) => node.textContent === permission);
  const label = code?.closest("label");
  const box = label?.querySelector("input[type=checkbox]");
  if (!box) throw new Error(`no checkbox for ${permission}`);
  return box as HTMLInputElement;
}

beforeEach(() => {
  state.calls.length = 0;
  state.failure = null;
  state.saveFailure = null;
});

afterEach(async () => {
  for (const root of roots.splice(0)) {
    await act(async () => root.unmount());
  }
  vi.unstubAllGlobals();
});

describe("custom roles page", () => {
  it("renders modules from the catalogue and the tenant's roles", async () => {
    const host = await render();

    expect(state.calls.map((call) => call.path)).toContain("/api/auth/permission-catalogue");
    expect(host.textContent).toContain("Quality assurance");
    expect(host.textContent).toContain("QA reviewer");
    expect(host.textContent).toContain("qa:read");
    // The page states the additive rule where an operator will read it.
    expect(host.textContent).toContain("never");
    expect(host.textContent).toContain("removes one");
  });

  it("disables a permission the caller cannot grant instead of hiding it", async () => {
    const host = await render();

    expect(checkboxFor(host, "qa:read").disabled).toBe(false);
    expect(checkboxFor(host, "qa:write").disabled).toBe(true);
    expect(checkboxFor(host, "billing:write").disabled).toBe(true);
    expect(host.textContent).toContain("not yours to grant");
  });

  it("sends exactly the ticked permissions when creating a role", async () => {
    const host = await render();

    const nameInput = host.querySelector("input") as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      setter?.call(nameInput, "Night shift");
      nameInput.dispatchEvent(new Event("input", { bubbles: true }));
    });

    await act(async () => {
      checkboxFor(host, "qa:read").click();
    });
    await act(async () => {
      findButton(host, "Create role").click();
    });

    const create = state.calls.find(
      (call) => call.path === "/api/auth/custom-roles" && call.init?.method === "POST",
    );
    expect(create).toBeDefined();
    const body = JSON.parse(String(create?.init?.body));
    expect(body.name).toBe("Night shift");
    expect(body.permissions).toEqual(["qa:read"]);
  });

  it("shows an escalation refusal verbatim and does not claim success", async () => {
    const host = await render();
    state.saveFailure = { status: 403, message: "you cannot grant a permission you do not hold" };

    // A name is required before the request is even made, so type one: the point
    // of this test is the server's refusal, not the client's own validation.
    const nameInput = host.querySelector("input") as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      setter?.call(nameInput, "Too broad");
      nameInput.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      findButton(host, "Create role").click();
    });

    expect(host.querySelector('[data-testid="roles-error"]')?.textContent).toContain(
      "you cannot grant a permission you do not hold",
    );
    expect(host.textContent).not.toContain("Created");
  });

  it("reports a failed load instead of pretending there are no roles", async () => {
    state.failure = { status: 403, message: "user:role_change required" };
    const host = await render();

    expect(host.querySelector('[data-testid="roles-load-error"]')?.textContent).toContain(
      "user:role_change required",
    );
    expect(host.textContent).not.toContain("No custom roles yet");
  });

  it("lists assignments for the selected role, including SCIM groups", async () => {
    const host = await render();

    await act(async () => {
      findButton(host, "QA reviewer").click();
    });

    const assignmentsCall = state.calls.find((call) => call.path.endsWith("/assignments"));
    expect(assignmentsCall?.path).toBe(
      `/api/auth/custom-roles/${state.roles[0].id}/assignments`,
    );
    expect(host.textContent).toContain("Person");
    expect(host.textContent).toContain("44444444-4444-4444-4444-444444444444");
  });

  it("previews effective permissions as role plus custom, never instead of it", async () => {
    const host = await render();

    const inputs = [...host.querySelectorAll("input")].filter(
      (element) => element.type === "text" || element.type === undefined,
    ) as HTMLInputElement[];
    const previewInput = inputs[inputs.length - 1];
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      setter?.call(previewInput, "44444444-4444-4444-4444-444444444444");
      previewInput.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      findButton(host, "Preview").click();
    });

    const previewCall = state.calls.find((call) =>
      call.path.startsWith("/api/auth/effective-permissions"),
    );
    expect(previewCall?.path).toContain("user_id=44444444-4444-4444-4444-444444444444");
    expect(host.textContent).toContain("viewer");
    expect(host.textContent).toContain("custom adds 2");
  });
});
