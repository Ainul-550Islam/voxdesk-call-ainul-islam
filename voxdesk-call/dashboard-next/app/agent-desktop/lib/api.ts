import { getToken } from "@/lib/auth";

export class DesktopApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
  ) {
    super(message);
    this.name = "DesktopApiError";
  }
}

type ApiOptions = {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  idempotencyKey?: string;
};

export async function api<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const token = getToken();
  if (!token) {
    throw new DesktopApiError("Sign in before using the agent desktop", 401, "authentication_required");
  }
  const response = await fetch(path, {
    method: options.method ?? "GET",
    headers: {
      Accept: "application/json",
      Authorization: `Bearer ${token}`,
      ...(options.body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(options.idempotencyKey ? { "Idempotency-Key": options.idempotencyKey } : {}),
    },
    ...(options.body === undefined ? {} : { body: JSON.stringify(options.body) }),
  });
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      throw new DesktopApiError("The server returned invalid JSON", response.status, "invalid_json");
    }
  }
  if (!response.ok) {
    const detail = typeof payload === "object" && payload !== null && "detail" in payload
      ? (payload as { detail: unknown }).detail
      : payload;
    const code = typeof detail === "object" && detail !== null && "code" in detail
      ? String((detail as { code: unknown }).code)
      : "http_error";
    const message = typeof detail === "object" && detail !== null && "message" in detail
      ? String((detail as { message: unknown }).message)
      : typeof detail === "string"
        ? detail
        : `Request failed with HTTP ${response.status}`;
    throw new DesktopApiError(message, response.status, code);
  }
  return payload as T;
}

export function requestKey(): string {
  if (!globalThis.crypto?.randomUUID) {
    throw new DesktopApiError("This browser cannot create a secure request key", 0, "webcrypto_required");
  }
  return globalThis.crypto.randomUUID();
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "The request failed";
}
