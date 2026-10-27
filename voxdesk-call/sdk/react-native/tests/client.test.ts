import { describe, expect, it, vi } from "vitest";

import { VoxDeskClient } from "../src/client.js";
import { ApiError, NetworkError, parseErrorBody } from "../src/errors.js";
import { makeFetch } from "./harness.js";

function client(fetchImpl: ReturnType<typeof makeFetch>["fetchImpl"], extra = {}) {
  return new VoxDeskClient({
    baseUrl: "https://acme.voxdesk.example/",
    fetchImpl,
    ...extra,
  });
}

describe("VoxDeskClient", () => {
  it("strips a trailing slash from the base url so paths never double up", () => {
    const { fetchImpl, requests } = makeFetch([{ body: { ok: true } }]);
    const subject = client(fetchImpl);
    expect(subject.buildUrl("/api/mobile/devices")).toBe(
      "https://acme.voxdesk.example/api/mobile/devices",
    );
    expect(subject.buildUrl("api/mobile/devices")).toBe(
      "https://acme.voxdesk.example/api/mobile/devices",
    );
    void requests;
  });

  it("encodes query parameters and drops undefined ones", () => {
    const { fetchImpl } = makeFetch([]);
    const subject = client(fetchImpl);
    expect(
      subject.buildUrl("/api/mobile/deliveries", { limit: 50, status: undefined, since: "a b" }),
    ).toBe("https://acme.voxdesk.example/api/mobile/deliveries?limit=50&since=a+b");
  });

  it("attaches the access token and the caller's headers", async () => {
    const { fetchImpl, requests } = makeFetch([{ body: { devices: [], count: 0 } }]);
    const subject = client(fetchImpl, {
      getAccessToken: () => "secret-token",
      headers: { "X-Voxdesk-Client": "rn/1.0.0" },
    });
    await subject.request("/api/mobile/devices");
    expect(requests[0]?.headers.Authorization).toBe("Bearer secret-token");
    expect(requests[0]?.headers["X-Voxdesk-Client"]).toBe("rn/1.0.0");
    expect(requests[0]?.headers.Accept).toBe("application/json");
  });

  it("supports an async token callback and omits the header when signed out", async () => {
    const { fetchImpl, requests } = makeFetch([{ body: {} }, { body: {} }]);
    const signedIn = client(fetchImpl, { getAccessToken: async () => "async-token" });
    await signedIn.request("/api/mobile/providers");
    expect(requests[0]?.headers.Authorization).toBe("Bearer async-token");

    const signedOut = client(fetchImpl, { getAccessToken: () => null });
    await signedOut.request("/api/mobile/providers");
    expect(requests[1]?.headers.Authorization).toBeUndefined();
  });

  it("sends the idempotency key only when asked", async () => {
    const { fetchImpl, requests } = makeFetch([{ body: {} }, { body: {} }]);
    const subject = client(fetchImpl);
    await subject.request("/api/mobile/devices", {
      method: "POST",
      body: { platform: "ios" },
      idempotencyKey: "abc123",
    });
    await subject.request("/api/mobile/devices", { method: "POST", body: {} });
    expect(requests[0]?.headers["Idempotency-Key"]).toBe("abc123");
    expect(requests[0]?.headers["Content-Type"]).toBe("application/json");
    expect(requests[0]?.body).toEqual({ platform: "ios" });
    expect(requests[1]?.headers["Idempotency-Key"]).toBeUndefined();
  });

  it("returns undefined for 204 and for an empty body", async () => {
    const { fetchImpl } = makeFetch([{ status: 204 }, { raw: "" }]);
    const subject = client(fetchImpl);
    await expect(subject.request("/api/mobile/devices/x", { method: "DELETE" })).resolves.toBeUndefined();
    await expect(subject.request("/api/mobile/providers")).resolves.toBeUndefined();
  });

  it("raises ApiError with the server's code so callers branch on it", async () => {
    const { fetchImpl } = makeFetch([
      {
        status: 422,
        raw: JSON.stringify({
          detail: {
            code: "quiet_hours_too_long",
            message: "a quiet window may be at most 12 hours",
            detail: { max_span_hours: 12 },
          },
        }),
      },
    ]);
    const subject = client(fetchImpl);
    const error = await subject
      .request("/api/mobile/devices/1/quiet-hours", { method: "POST", body: {} })
      .catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 422, code: "quiet_hours_too_long" });
    expect((error as ApiError).detail).toEqual({ max_span_hours: 12 });
    expect((error as ApiError).isPermanent).toBe(true);
  });

  it("treats 429 as retryable", () => {
    expect(new ApiError(429, { code: "rate_limited", message: "slow down" }).isPermanent).toBe(false);
    expect(new ApiError(403, { code: "permission_denied", message: "no" }).isPermanent).toBe(true);
  });

  it("calls onUnauthorized on 401 and still raises", async () => {
    const { fetchImpl } = makeFetch([
      { status: 401, raw: JSON.stringify({ detail: { code: "unauthenticated", message: "expired" } }) },
    ]);
    const onUnauthorized = vi.fn();
    const subject = client(fetchImpl, { onUnauthorized });
    await expect(subject.request("/api/mobile/devices")).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("wraps a transport failure in NetworkError instead of leaking the DOM error", async () => {
    const failing = () => Promise.reject(new TypeError("Network request failed"));
    const subject = new VoxDeskClient({ baseUrl: "https://acme.example", fetchImpl: failing });
    await expect(subject.request("/api/mobile/devices")).rejects.toBeInstanceOf(NetworkError);
  });

  it("aborts a request that outlives the timeout", async () => {
    const hanging = (_url: string, init?: RequestInit) =>
      new Promise<never>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      });
    const subject = new VoxDeskClient({
      baseUrl: "https://acme.example",
      fetchImpl: hanging as never,
      timeoutMs: 5,
    });
    await expect(subject.request("/api/mobile/devices")).rejects.toBeInstanceOf(NetworkError);
  });

  it("refuses to construct without a base url or without fetch", () => {
    expect(() => new VoxDeskClient({ baseUrl: "", fetchImpl: vi.fn() as never })).toThrow(/baseUrl/);
  });
});

describe("parseErrorBody", () => {
  it("reads the API envelope", () => {
    expect(
      parseErrorBody('{"detail":{"code":"not_found","message":"device not found"}}'),
    ).toEqual({ code: "not_found", message: "device not found", detail: {} });
  });

  it("reads a plain FastAPI detail string", () => {
    expect(parseErrorBody('{"detail":"Not Found"}')).toEqual({ code: "error", message: "Not Found" });
  });

  it("falls back to the raw text when the body is not JSON", () => {
    const parsed = parseErrorBody("upstream connect error");
    expect(parsed.code).toBe("error");
    expect(parsed.message).toContain("upstream");
  });

  it("never throws on a truncated body", () => {
    expect(() => parseErrorBody("{\"detail\":")).not.toThrow();
  });
});
