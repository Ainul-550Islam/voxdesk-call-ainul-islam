import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ConnectionDemoPage from '@/app/dashboard/connection-demo/page';
import { api } from '@/lib/api';

const scenario = vi.hoisted(() => ({ status: 401 }));
vi.mock('@/lib/api', () => {
  class ApiError extends Error {
    status: number;
    constructor(status: number) { super('Rejected request'); this.status = status; }
  }
  const methods = ['specializedAgents', 'workflows', 'voiceProfiles', 'complianceFrameworks',
    'clauseLibrary', 'qmsProviders', 'translationJobs', 'connectors'];
  return { BASE_URL: '', ApiError, api: Object.fromEntries(methods.map(name => [name, vi.fn(async () => {
    if (scenario.status !== 200) throw new ApiError(scenario.status);
    return [];
  })])) };
});

afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });

describe('Observed connection results', () => {
  it.each([401, 403, 422, 500])('does not turn HTTP %s into a successful operation', async status => {
    scenario.status = status;
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ status: 'ok' }) })));
    const host = document.createElement('div');
    const root = createRoot(host);
    try {
      await act(async () => { root.render(<ConnectionDemoPage />); });
      const rows = [...host.querySelectorAll('tbody tr')];
      expect(rows).toHaveLength(9);
      expect(rows[0].textContent).toContain('Success');
      rows.slice(1).forEach(row => {
        expect(row.textContent).toContain('Error');
        expect(row.textContent).not.toContain('Success');
      });
      expect(api.complianceFrameworks).toHaveBeenCalledTimes(1);
      expect(host.textContent).toContain('1 succeeded; 8 failed; 0 pending');
    } finally { await act(async () => root.unmount()); }
  });

  it('reports success only after successful responses', async () => {
    scenario.status = 200;
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ status: 'ok' }) })));
    const host = document.createElement('div');
    const root = createRoot(host);
    try {
      await act(async () => { root.render(<ConnectionDemoPage />); });
      expect(host.textContent).toContain('9 succeeded; 0 failed; 0 pending');
    } finally { await act(async () => root.unmount()); }
  });
});
