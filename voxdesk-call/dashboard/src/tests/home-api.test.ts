import { describe, expect, it, vi } from 'vitest';
import { getAnalyticsSummary, getPublicHealth, getPublicHome } from '../api/home';

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('public home API path contracts', () => {
  it('uses the backend public endpoints without duplicating the configured /api base path', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse({ status: 'ok', data: {} }));
    vi.stubGlobal('fetch', fetchMock);

    await getPublicHome();
    await getAnalyticsSummary();
    await getPublicHealth();

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      '/api/v1/public/home',
      '/api/v1/public/analytics/summary',
      '/api/v1/public/health',
    ]);
  });
});
