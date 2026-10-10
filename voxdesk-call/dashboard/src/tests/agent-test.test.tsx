/** dashboard/src/tests/agent-test.test.tsx — Contract tests for agent-test API client */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { apiClient } from '../api/client';
import {
  createTestSession,
  postTestEvent,
  getTestSession,
} from '../api/agent-test';

describe('agent-test API contract', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('creates and advances test sessions via /api/v1/agents/{id}/test and /api/v1/agent-tests/{id}', async () => {
    vi.spyOn(apiClient, 'post')
      .mockResolvedValueOnce({
        session: { id: 'sess-1', agent_id: 'agt-1', state: 'ACTIVE', created_at: '2026-10-09T00:00:00Z', transcript: [] },
      })
      .mockResolvedValueOnce({
        session: { id: 'sess-1', agent_id: 'agt-1', state: 'ACTIVE', created_at: '2026-10-09T00:00:00Z', transcript: [{ role: 'user', text: 'Hi' }] },
      });
    vi.spyOn(apiClient, 'get').mockResolvedValueOnce({
      session: { id: 'sess-1', agent_id: 'agt-1', state: 'COMPLETED', created_at: '2026-10-09T00:00:00Z', transcript: [] },
    });

    const created = await createTestSession('agt-1');
    expect(created.id).toBe('sess-1');

    const updated = await postTestEvent('sess-1', 'utterance', { text: 'Hi' });
    expect(updated.transcript).toHaveLength(1);

    const fetched = await getTestSession('sess-1');
    expect(fetched.state).toBe('COMPLETED');
  });

  it('propagates event errors instead of fabricating an IDLE session', async () => {
    vi.spyOn(apiClient, 'post').mockRejectedValueOnce(new Error('Session closed'));
    await expect(postTestEvent('sess-1', 'utterance', { text: 'Hi' })).rejects.toThrow('Session closed');
  });
});
