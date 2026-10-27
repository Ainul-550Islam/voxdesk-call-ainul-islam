/** Test doubles for the SDK: a scripted fetch, no network, no timers left running. */

import type { FetchLike, ResponseLike } from "../src/types.js";

export interface RecordedRequest {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: unknown;
}

export interface ScriptedResponse {
  status?: number;
  body?: unknown;
  raw?: string;
}

export function jsonResponse(status: number, body: unknown): ResponseLike {
  const text = body === undefined ? "" : JSON.stringify(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "application/json" },
    text: async () => text,
    json: async () => body,
  };
}

export function makeFetch(responses: ScriptedResponse[]): {
  fetchImpl: FetchLike;
  requests: RecordedRequest[];
} {
  const requests: RecordedRequest[] = [];
  let index = 0;
  const fetchImpl: FetchLike = async (url, init) => {
    const headers: Record<string, string> = {};
    const rawHeaders = (init?.headers ?? {}) as Record<string, string>;
    for (const [key, value] of Object.entries(rawHeaders)) {
      headers[key] = value;
    }
    const rawBody = init?.body;
    requests.push({
      url,
      method: init?.method ?? "GET",
      headers,
      body: typeof rawBody === "string" ? JSON.parse(rawBody) : undefined,
    });
    const scripted = responses[index];
    index += 1;
    if (!scripted) {
      throw new Error(`unexpected request ${index}: ${url}`);
    }
    const status = scripted.status ?? 200;
    if (scripted.raw !== undefined) {
      return {
        ok: status >= 200 && status < 300,
        status,
        headers: { get: () => "text/plain" },
        text: async () => scripted.raw as string,
        json: async () => JSON.parse(scripted.raw as string),
      };
    }
    return jsonResponse(status, scripted.body);
  };
  return { fetchImpl, requests };
}

export const TOKEN = "test-access-token";

export function tenantHeaders(): Record<string, string> {
  return { "X-Voxdesk-Client": "rn/1.0.0" };
}
