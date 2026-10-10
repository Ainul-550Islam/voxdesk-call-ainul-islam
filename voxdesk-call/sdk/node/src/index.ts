/**
 * `@voxdesk/node` (`sdk/node/src/index.ts`)
 *
 * Typed Node.js server client for the VoxDesk OpenAPI contract:
 * - Calls, Web Calls, Batch Calls, Agents, Agent Versions, Phone Numbers, Webhooks
 * - `verifyWebhookSignature()` using constant-time HMAC-SHA256 comparison
 * - Cursor / offset pagination helpers (`paginatePages`, `collectAllPages`)
 */

import { createHmac, timingSafeEqual } from "crypto";

export interface VoxDeskNodeClientOptions {
  apiKey: string;
  baseUrl?: string;
  timeoutMs?: number;
  fetchImpl?: typeof fetch;
}

export interface CreateWebCallInput {
  agentId: string;
  version?: number;
  dynamicVars?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  origin?: string;
  ttlSeconds?: number;
}

export interface WebCallSession {
  call_id: string;
  tenant_id: string;
  agent_id: string;
  agent_version: number;
  direction: "web";
  status: string;
  transport: "ws-protobuf" | "small-webrtc";
  url: string;
  offer_url?: string;
  access_token: string;
  expires_at: string;
  sample_rate: number;
}

export interface CreateOutboundCallInput {
  agentId: string;
  toNumber: string;
  fromNumber?: string;
  dynamicVars?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export interface CreateBatchCallsInput {
  agentId: string;
  recipients: Array<{
    toNumber: string;
    dynamicVars?: Record<string, unknown>;
    metadata?: Record<string, unknown>;
  }>;
  fromNumber?: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total?: number;
  next_cursor?: string | null;
  has_more?: boolean;
}

export class VoxDeskApiError extends Error {
  public readonly statusCode: number;
  public readonly body: unknown;

  constructor(statusCode: number, body: unknown) {
    super(`VoxDesk API error (HTTP ${statusCode})`);
    this.name = "VoxDeskApiError";
    this.statusCode = statusCode;
    this.body = body;
  }
}

export function verifyWebhookSignature(
  payload: string | Buffer,
  signatureHeader: string,
  secret: string,
  options?: { timestampHeader?: string; toleranceSeconds?: number }
): boolean {
  if (!signatureHeader || !secret) {
    return false;
  }
  const rawBuffer =
    typeof payload === "string" ? Buffer.from(payload, "utf-8") : payload;
  const normalizedSig = signatureHeader.trim().replace(/^sha256=/i, "");
  const sigBuf = Buffer.from(normalizedSig, "hex");
  if (sigBuf.length !== 32) {
    return false;
  }

  if (options?.timestampHeader) {
    const ts = Number.parseInt(options.timestampHeader.trim(), 10);
    if (!Number.isFinite(ts)) {
      return false;
    }
    const tolerance = options.toleranceSeconds ?? 300;
    const nowSec = Math.floor(Date.now() / 1000);
    if (Math.abs(nowSec - ts) > tolerance) {
      return false;
    }
    const signedPayload = Buffer.concat([
      Buffer.from(`${ts}.`, "utf-8"),
      rawBuffer,
    ]);
    const expectedTs = createHmac("sha256", secret)
      .update(signedPayload)
      .digest();
    if (
      expectedTs.length === sigBuf.length &&
      timingSafeEqual(expectedTs, sigBuf)
    ) {
      return true;
    }
  }

  const expected = createHmac("sha256", secret).update(rawBuffer).digest();
  return expected.length === sigBuf.length && timingSafeEqual(expected, sigBuf);
}

export async function* paginatePages<T>(
  fetchPage: (cursor?: string) => Promise<PaginatedResponse<T>>
): AsyncGenerator<T[], void, unknown> {
  let cursor: string | undefined = undefined;
  while (true) {
    const page = await fetchPage(cursor);
    yield page.items ?? [];
    if (!page.next_cursor || page.has_more === false) {
      break;
    }
    cursor = page.next_cursor;
  }
}

export async function collectAllPages<T>(
  fetchPage: (cursor?: string) => Promise<PaginatedResponse<T>>
): Promise<T[]> {
  const results: T[] = [];
  for await (const items of paginatePages(fetchPage)) {
    results.push(...items);
  }
  return results;
}

export class VoxDeskNodeClient {
  private readonly apiKey: string;
  private readonly baseUrl: string;
  private readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;

  constructor(options: VoxDeskNodeClientOptions) {
    if (!options?.apiKey || !options.apiKey.trim()) {
      throw new Error("apiKey is required");
    }
    this.apiKey = options.apiKey.trim();
    this.baseUrl = (options.baseUrl ?? "http://localhost:8000").replace(
      /\/+$/,
      ""
    );
    this.timeoutMs = options.timeoutMs ?? 15000;
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

  public async request<T = unknown>(
    method: string,
    path: string,
    body?: unknown,
    headers?: Record<string, string>
  ): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const reqHeaders: Record<string, string> = {
        Authorization: `Bearer ${this.apiKey}`,
        Accept: "application/json",
        ...(headers ?? {}),
      };
      if (body !== undefined) {
        reqHeaders["Content-Type"] = "application/json";
      }
      const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
        method,
        headers: reqHeaders,
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });

      if (!response.ok) {
        const errBody = await response.json().catch(() => null);
        throw new VoxDeskApiError(response.status, errBody);
      }
      if (response.status === 204) {
        return undefined as unknown as T;
      }
      return (await response.json()) as T;
    } finally {
      clearTimeout(timer);
    }
  }

  public async createWebCall(
    input: CreateWebCallInput
  ): Promise<WebCallSession> {
    return this.request<WebCallSession>("POST", "/api/web-calls", {
      agent_id: input.agentId,
      version: input.version,
      dynamic_vars: input.dynamicVars ?? {},
      metadata: input.metadata ?? {},
      origin: input.origin,
      ttl_seconds: input.ttlSeconds ?? 300,
    });
  }

  public async getWebCall(callId: string): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "GET",
      `/api/web-calls/${encodeURIComponent(callId)}`
    );
  }

  public async endWebCall(
    callId: string,
    reason = "ended_by_api"
  ): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "POST",
      `/api/web-calls/${encodeURIComponent(callId)}/end`,
      { reason }
    );
  }

  public async createCall(
    input: CreateOutboundCallInput
  ): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "POST",
      "/api/v1/telephony/calls",
      {
        agent_id: input.agentId,
        to_number: input.toNumber,
        from_number: input.fromNumber,
        dynamic_vars: input.dynamicVars ?? {},
        metadata: input.metadata ?? {},
      }
    );
  }

  public async getCall(callId: string): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "GET",
      `/api/calls/${encodeURIComponent(callId)}`
    );
  }

  public async createBatchCalls(
    input: CreateBatchCallsInput
  ): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "POST",
      "/api/calls/outbound/bulk",
      {
        agent_id: input.agentId,
        from_number: input.fromNumber,
        calls: input.recipients.map((r) => ({
          to_number: r.toNumber,
          dynamic_vars: r.dynamicVars ?? {},
          metadata: r.metadata ?? {},
        })),
      }
    );
  }

  public async subscribeWebhook(input: {
    url: string;
    events: string[];
    secret?: string;
  }): Promise<{ id: string; url: string; events: string[] }> {
    return this.request<{ id: string; url: string; events: string[] }>(
      "POST",
      "/api/webhooks",
      input
    );
  }

  public async unsubscribeWebhook(webhookId: string): Promise<void> {
    await this.request<void>(
      "DELETE",
      `/api/webhooks/${encodeURIComponent(webhookId)}`
    );
  }
}
