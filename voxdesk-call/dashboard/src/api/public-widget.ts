/**
 * dashboard/src/api/public-widget.ts
 * Restricted public widget API client (/api/v1/public/widget/*).
 * Uses only X-VoxDesk-Public-Key or X-VoxDesk-Widget-Session headers — never private workspace JWTs.
 */

import type {
  PublicWidgetBootstrapConfig,
  PublicWidgetMessageSendResponse,
  PublicWidgetSessionMode,
  PublicWidgetSessionRecord,
} from './types/public-widget';

export class PublicWidgetApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'PublicWidgetApiError';
    this.status = status;
    this.code = code;
  }
}

async function widgetFetch<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: 'application/json',
    ...(options.body ? { 'Content-Type': 'application/json' } : {}),
    ...((options.headers as Record<string, string>) || {}),
  };

  const response = await fetch(path, {
    ...options,
    headers,
  });

  if (!response.ok) {
    let code = `HTTP_${response.status}`;
    let message = response.statusText || 'Widget request failed';
    try {
      const body = await response.json();
      if (body?.error?.code) code = String(body.error.code);
      if (body?.error?.message) message = String(body.error.message);
      else if (typeof body?.detail === 'string') message = body.detail;
    } catch {
      // ignore json parse failure
    }
    throw new PublicWidgetApiError(response.status, code, message);
  }

  return (await response.json()) as T;
}

export async function fetchPublicWidgetBootstrap(params: {
  publicKey: string;
  origin?: string;
}): Promise<PublicWidgetBootstrapConfig> {
  const headers: Record<string, string> = {
    'X-VoxDesk-Public-Key': params.publicKey.trim(),
  };
  if (params.origin) {
    headers['X-Widget-Origin'] = params.origin;
  }
  return widgetFetch<PublicWidgetBootstrapConfig>('/api/v1/public/widget/config', {
    method: 'GET',
    headers,
  });
}

export async function startPublicWidgetSession(params: {
  publicKey: string;
  mode: PublicWidgetSessionMode;
  visitorId?: string;
  metadata?: Record<string, unknown>;
  origin?: string;
}): Promise<PublicWidgetSessionRecord> {
  const headers: Record<string, string> = {
    'X-VoxDesk-Public-Key': params.publicKey.trim(),
  };
  if (params.origin) {
    headers['X-Widget-Origin'] = params.origin;
  }
  return widgetFetch<PublicWidgetSessionRecord>('/api/v1/public/widget/sessions', {
    method: 'POST',
    headers,
    body: JSON.stringify({
      public_key: params.publicKey.trim(),
      mode: params.mode,
      visitor_id: params.visitorId || undefined,
      metadata: params.metadata || {},
    }),
  });
}

export async function getPublicWidgetSession(params: {
  sessionId: string;
  sessionToken: string;
}): Promise<PublicWidgetSessionRecord> {
  return widgetFetch<PublicWidgetSessionRecord>(
    `/api/v1/public/widget/sessions/${encodeURIComponent(params.sessionId)}`,
    {
      method: 'GET',
      headers: {
        'X-VoxDesk-Widget-Session': params.sessionToken,
      },
    },
  );
}

export async function sendPublicWidgetMessage(params: {
  sessionId: string;
  sessionToken: string;
  message: string;
}): Promise<PublicWidgetMessageSendResponse> {
  return widgetFetch<PublicWidgetMessageSendResponse>(
    `/api/v1/public/widget/sessions/${encodeURIComponent(params.sessionId)}/messages`,
    {
      method: 'POST',
      headers: {
        'X-VoxDesk-Widget-Session': params.sessionToken,
      },
      body: JSON.stringify({
        message: params.message,
      }),
    },
  );
}

export async function endPublicWidgetSession(params: {
  sessionId: string;
  sessionToken: string;
  reason?: string;
}): Promise<PublicWidgetSessionRecord> {
  return widgetFetch<PublicWidgetSessionRecord>(
    `/api/v1/public/widget/sessions/${encodeURIComponent(params.sessionId)}/end`,
    {
      method: 'POST',
      headers: {
        'X-VoxDesk-Widget-Session': params.sessionToken,
      },
      body: JSON.stringify({
        reason: params.reason || 'visitor_ended',
      }),
    },
  );
}
