import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, apiClient } from '../api/client';
import {
  bootstrapSession,
  clearSession,
  getAccessToken,
  getAuthSession,
  refreshAccessToken,
  setSessionAccessToken,
} from '../lib/session';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('foundation session and API client', () => {
  beforeEach(() => {
    clearSession();
    window.localStorage.clear();
  });

  it('keeps bearer tokens in memory and removes legacy Web Storage tokens', () => {
    window.localStorage.setItem('voxdesk_access_token', 'legacy-token');
    setSessionAccessToken('memory-token');

    expect(getAccessToken()).toBe('memory-token');
    expect(window.localStorage.getItem('voxdesk_access_token')).toBeNull();
  });

  it('restores a session through the HttpOnly refresh cookie and /auth/me', async () => {
    const session = {
      user: { id: 'u1', email: 'owner@example.com', full_name: 'Owner', role: 'owner', tenant_id: 't1' },
      tenant: { id: 't1' },
      permissions: ['analytics:read'],
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json({ access_token: 'refreshed-token' }))
      .mockResolvedValueOnce(json(session));
    vi.stubGlobal('fetch', fetchMock);

    await expect(bootstrapSession()).resolves.toEqual(session);

    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      '/auth/refresh',
      '/auth/me',
    ]);
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get('Authorization')).toBe('Bearer refreshed-token');
  });

  it('refreshes once on a 401 and replays with the new bearer token', async () => {
    setSessionAccessToken('expired-token');
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json({ detail: 'expired' }, 401))
      .mockResolvedValueOnce(json({ access_token: 'new-token' }))
      .mockResolvedValueOnce(json({ items: [1] }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(apiClient.get<{ items: number[] }>('/api/agents')).resolves.toEqual({ items: [1] });

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(String(fetchMock.mock.calls[1][0])).toBe('/auth/refresh');
    expect(new Headers(fetchMock.mock.calls[2][1]?.headers).get('Authorization')).toBe('Bearer new-token');
  });

  it('normalizes structured backend errors without hiding their safe message', async () => {
    setSessionAccessToken('token');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({
      error: { code: 'not_configured', message: 'Provider is not configured' },
    }, 503)));

    const request = apiClient.get('/api/integrations/crm');
    await expect(request).rejects.toMatchObject<ApiError>({
      code: 'NOT_CONFIGURED',
      status: 503,
      message: 'Provider is not configured',
    });
  });

  it('does not resurrect a cleared session when an older refresh completes', async () => {
    let complete!: (response: Response) => void;
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => {
      complete = resolve;
    })));

    const pending = refreshAccessToken();
    clearSession();
    complete(json({ access_token: 'stale-late-token' }));

    await expect(pending).resolves.toBeNull();
    expect(getAccessToken()).toBeNull();
  });

  it('lets a newer login bootstrap while an older /auth/me is in flight', async () => {
    const oldSession = {
      user: { id: 'old-user', email: 'old@example.com', full_name: 'Old', role: 'owner', tenant_id: 'old-tenant' },
      tenant: { id: 'old-tenant' }, permissions: ['analytics:read'],
    };
    const newSession = {
      user: { id: 'new-user', email: 'new@example.com', full_name: 'New', role: 'owner', tenant_id: 'new-tenant' },
      tenant: { id: 'new-tenant' }, permissions: ['analytics:read'],
    };
    let completeOld!: (response: Response) => void;
    const fetchMock = vi.fn((_: unknown, init?: RequestInit) => {
      const token = new Headers(init?.headers).get('Authorization');
      if (token === 'Bearer old-token') {
        return new Promise<Response>((resolve) => { completeOld = resolve; });
      }
      return Promise.resolve(json(newSession));
    });
    vi.stubGlobal('fetch', fetchMock);

    setSessionAccessToken('old-token');
    const oldPending = bootstrapSession();
    setSessionAccessToken('new-token');
    const newPending = bootstrapSession();
    completeOld(json(oldSession));

    await expect(oldPending).resolves.toBeNull();
    await expect(newPending).resolves.toEqual(newSession);
    expect(getAuthSession()).toEqual(newSession);
    expect(getAccessToken()).toBe('new-token');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
