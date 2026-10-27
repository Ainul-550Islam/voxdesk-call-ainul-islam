/** Error surface of the SDK.
 *
 * Two classes, because the callers behave differently:
 *
 * * `ApiError` — the server answered and refused. `code` is the machine-readable
 *   value from the API envelope (`quiet_hours_too_long`, `device_not_found`, …);
 *   `status` is the HTTP status. The UI branches on `code`, never on `message`.
 * * `NetworkError` — no answer arrived (timeout, offline, DNS). Retryable, and the
 *   caller must not show it as "the server rejected your request".
 */

import type { ApiErrorDetail } from "./types.js";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: Record<string, unknown>;

  constructor(status: number, body: ApiErrorDetail) {
    super(body.message || `request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = body.code || "unknown_error";
    this.detail = body.detail ?? {};
  }

  /** 4xx that a naive retry will never fix. */
  get isPermanent(): boolean {
    return this.status >= 400 && this.status < 500 && this.status !== 429;
  }
}

export class NetworkError extends Error {
  readonly cause?: unknown;

  constructor(message: string, cause?: unknown) {
    super(message);
    this.name = "NetworkError";
    this.cause = cause;
  }
}

/** Parse the API's error envelope: `{detail: {code, message, detail}}` or `{detail: "..."}`. */
export function parseErrorBody(raw: string): ApiErrorDetail {
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (typeof parsed === "object" && parsed !== null) {
      const outer = (parsed as { detail?: unknown }).detail;
      if (typeof outer === "string") {
        return { code: "error", message: outer };
      }
      if (typeof outer === "object" && outer !== null) {
        const envelope = outer as { code?: unknown; message?: unknown; detail?: unknown };
        return {
          code: typeof envelope.code === "string" ? envelope.code : "error",
          message: typeof envelope.message === "string" ? envelope.message : "request failed",
          detail:
            typeof envelope.detail === "object" && envelope.detail !== null
              ? (envelope.detail as Record<string, unknown>)
              : {},
        };
      }
    }
  } catch {
    // Not JSON: FastAPI's plain-text 500 path, a proxy error page, etc.
  }
  return { code: "error", message: raw.slice(0, 300) || "request failed" };
}
