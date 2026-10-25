import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, DesktopApiError } from "../lib/api";

describe("agent desktop API boundary", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("requires the existing session access token", async () => {
    await expect(api("/api/cc2/softphone/calls")).rejects.toMatchObject({
      status: 401,
      code: "authentication_required",
    });
  });

  it("sends bearer auth and parses stable domain errors", async () => {
    window.sessionStorage.setItem("voxdesk.access_token", "token-1");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      detail: { code: "provider_outcome_ambiguous", message: "Reconcile before retrying" },
    }), { status: 409 }));
    const request = api("/api/cc2/softphone/calls", { method: "POST", body: { destination: "+15551234567" } });
    await expect(request).rejects.toEqual(new DesktopApiError("Reconcile before retrying", 409, "provider_outcome_ambiguous"));
    expect(fetchMock.mock.calls[0]?.[1]?.headers).toMatchObject({ Authorization: "Bearer token-1" });
  });
});
