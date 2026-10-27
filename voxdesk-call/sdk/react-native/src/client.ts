/** The HTTP client: one place that knows how to talk to the API.
 *
 * Rules it enforces, so no caller has to remember them:
 *
 * * **Joins the URL safely.** A base URL with a trailing slash and a path with a
 *   leading slash produce `//` — and a double slash is a 404 on some proxies.
 * * **Attaches the access token** from the caller's callback, and never logs it.
 * * **Times out.** React Native's fetch has no default timeout; a request that
 *   hangs forever looks like a frozen screen.
 * * **Decodes the error envelope** into `ApiError`, so callers branch on `code`.
 * * **Sends `Idempotency-Key`** on mutating requests when one is supplied. The
 *   server deduplicates on it; without it, a retried "publish rota" publishes twice.
 */

import { ApiError, NetworkError, parseErrorBody } from "./errors.js";
import type { ClientOptions, FetchLike, RequestOptions } from "./types.js";

const DEFAULT_TIMEOUT_MS = 15_000;

export class VoxDeskClient {
  private readonly baseUrl: string;
  private readonly getAccessToken: (() => string | null | Promise<string | null>) | undefined;
  private readonly onUnauthorized: (() => void | Promise<void>) | undefined;
  private readonly extraHeaders: Record<string, string>;
  private readonly timeoutMs: number;
  private readonly fetchImpl: FetchLike;

  constructor(options: ClientOptions) {
    if (!options.baseUrl) {
      throw new Error("baseUrl is required");
    }
    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.getAccessToken = options.getAccessToken;
    this.onUnauthorized = options.onUnauthorized;
    this.extraHeaders = options.headers ?? {};
    this.timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    const injected = options.fetchImpl;
    if (injected) {
      this.fetchImpl = injected;
    } else if (typeof fetch === "function") {
      this.fetchImpl = ((url, init) => fetch(url, init)) as FetchLike;
    } else {
      throw new Error("no fetch implementation available; pass one with options.fetchImpl");
    }
  }

  buildUrl(path: string, query?: Record<string, string | number | boolean | undefined>): string {
    const normalizedPath = path.startsWith("/") ? path : `/${path}`;
    const url = `${this.baseUrl}${normalizedPath}`;
    if (!query) {
      return url;
    }
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined) continue;
      params.append(key, String(value));
    }
    const rendered = params.toString();
    return rendered ? `${url}?${rendered}` : url;
  }

  async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const method = options.method ?? "GET";
    const url = this.buildUrl(path, options.query);
    const headers: Record<string, string> = {
      Accept: "application/json",
      ...this.extraHeaders,
      ...(options.headers ?? {}),
    };

    const token = this.getAccessToken ? await this.getAccessToken() : null;
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
    if (options.body !== undefined) {
      headers["Content-Type"] = "application/json";
    }
    if (options.idempotencyKey) {
      headers["Idempotency-Key"] = options.idempotencyKey;
    }

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), options.timeoutMs ?? this.timeoutMs);
    const init: RequestInit = { method, headers, signal: controller.signal };
    if (options.body !== undefined) {
      init.body = JSON.stringify(options.body);
    }
    let response;
    try {
      response = await this.fetchImpl(url, init);
    } catch (error) {
      // A timeout aborts with a DOMException named AbortError; offline surfaces a
      // TypeError. Both mean "no answer", which is what NetworkError is for.
      throw new NetworkError(`request to ${path} failed`, error);
    } finally {
      clearTimeout(timeout);
    }

    if (response.status === 401 && this.onUnauthorized) {
      await this.onUnauthorized();
    }

    if (!response.ok) {
      const raw = await response.text();
      throw new ApiError(response.status, parseErrorBody(raw));
    }
    if (response.status === 204) {
      return undefined as T;
    }
    const raw = await response.text();
    if (!raw) {
      return undefined as T;
    }
    return JSON.parse(raw) as T;
  }
}
