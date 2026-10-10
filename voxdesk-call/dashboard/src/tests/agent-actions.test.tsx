/** dashboard/src/tests/agent-actions.test.tsx — Contract tests for agent-actions API client */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { apiClient } from '../api/client';
import {
  getAgentActions,
  listAgentActions,
  archiveAgent,
  restoreAgent,
  deleteAgent,
} from '../api/agent-actions';

describe('agent-actions API contract', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('calls real backend routes for agent lifecycle actions', async () => {
    const getSpy = vi
      .spyOn(apiClient, 'get')
      .mockResolvedValueOnce({ id: 'agt-1', name: 'Receptionist' })
      .mockResolvedValueOnce([{ id: 'agt-1', name: 'Receptionist' }]);
    const postSpy = vi
      .spyOn(apiClient, 'post')
      .mockResolvedValueOnce({ status: 'archived' })
      .mockResolvedValueOnce({ status: 'active' });
    const delSpy = vi
      .spyOn(apiClient, 'delete')
      .mockResolvedValueOnce({ deleted: true });

    const single = await getAgentActions('agt-1');
    expect(single.id).toBe('agt-1');
    expect(getSpy).toHaveBeenCalledWith('/api/agents/agt-1');

    const list = await listAgentActions();
    expect(list).toHaveLength(1);
    expect(getSpy).toHaveBeenCalledWith('/api/agents');

    await archiveAgent('agt-1', 'seasonal pause');
    expect(postSpy).toHaveBeenCalledWith('/api/v1/agents/agt-1/archive', {
      reason: 'seasonal pause',
    });

    await restoreAgent('agt-1');
    expect(postSpy).toHaveBeenCalledWith('/api/v1/agents/agt-1/restore', {});

    await deleteAgent('agt-1');
    expect(delSpy).toHaveBeenCalledWith('/api/v1/agents/agt-1');
  });

  it('propagates backend errors instead of swallowing into null or []', async () => {
    vi.spyOn(apiClient, 'get').mockRejectedValueOnce(new Error('HTTP 500'));
    await expect(getAgentActions('agt-1')).rejects.toThrow('HTTP 500');
  });
});
