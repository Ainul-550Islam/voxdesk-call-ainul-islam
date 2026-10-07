import { afterEach, describe, expect, it, vi } from 'vitest';
import { apiRequest, resolveApiUrl } from '../api/client';
import { loginWithPassword } from '../api/public-site';

afterEach(() => vi.unstubAllGlobals());

describe('shared API paths against the actual backend contract', () => {
  it.each([
    ['/api', '/api/agents', '/api/agents'],
    ['/api/', '/api/v1/agents', '/api/v1/agents'],
    ['/api', '/agents', '/api/agents'],
    ['', '/api/agents', '/api/agents'],
    ['https://api.example.com/api', '/api/agents', 'https://api.example.com/api/agents'],
    ['/backend', '/api/agents', '/backend/api/agents'],
  ])('joins %s and %s exactly once', (base, path, expected) => {
    expect(resolveApiUrl(base, path)).toBe(expected);
  });

  it('fetches the real agent route with the default API base', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ items: [] }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    await apiRequest('/api/agents');
    expect(fetchMock.mock.calls[0][0]).toBe('/api/agents');
  });

  it('sends the backend LoginIn next field rather than an ignored next_path field', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ access_token: 'contract-token' }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    await loginWithPassword({ email: 'owner@example.com', password: 'test-only-password', next_path: '/app/overview' });
    expect(fetchMock.mock.calls[0][0]).toBe('/auth/login');
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(body.next).toBe('/app/overview');
    expect(body).not.toHaveProperty('next_path');
  });
});
