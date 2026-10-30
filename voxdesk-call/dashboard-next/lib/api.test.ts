import { afterEach, describe, expect, it, vi } from "vitest";
import { api, BASE_URL } from "./api";

describe("API route and method contract", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("updates an agent draft with the backend's PUT route", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "agt-1" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      api.patchAgent("agt 1", { name: "Support" }, '"draft-etag"'),
    ).resolves.toEqual({ id: "agt-1" });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${BASE_URL}/api/agents/agt%201`);
    expect(init.method).toBe("PUT");
    expect(new Headers(init.headers).get("If-Match")).toBe('"draft-etag"');
    expect(JSON.parse(String(init.body))).toEqual({ name: "Support" });
  });
});
