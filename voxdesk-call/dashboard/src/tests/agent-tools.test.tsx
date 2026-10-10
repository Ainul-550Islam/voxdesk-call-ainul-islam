/** dashboard/src/tests/agent-tools.test.tsx — Contract tests for agent-tools API client */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { apiClient } from '../api/client';
import {
  listAgentTools,
  createAgentTool,
  deleteAgentTool,
} from '../api/agent-tools';

describe('agent-tools API contract', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('lists, creates, and deletes agent tools via /api/agents/{id}/tools', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValueOnce([
      { id: 'tool-1', agent_id: 'agt-1', name: 'lookup_order', is_enabled: true },
    ]);
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValueOnce({
      id: 'tool-2',
      agent_id: 'agt-1',
      name: 'cancel_order',
      is_enabled: true,
    });
    const delSpy = vi.spyOn(apiClient, 'delete').mockResolvedValueOnce(undefined);

    const tools = await listAgentTools('agt-1');
    expect(tools).toHaveLength(1);
    expect(getSpy).toHaveBeenCalledWith('/api/agents/agt-1/tools');

    const created = await createAgentTool('agt-1', { name: 'cancel_order' });
    expect(created.name).toBe('cancel_order');
    expect(postSpy).toHaveBeenCalledWith(
      '/api/agents/agt-1/tools',
      expect.objectContaining({ name: 'cancel_order' }),
    );

    await deleteAgentTool('agt-1', 'tool-2');
    expect(delSpy).toHaveBeenCalledWith('/api/agents/agt-1/tools/tool-2');
  });

  it('propagates backend errors when tool registry fails', async () => {
    vi.spyOn(apiClient, 'get').mockRejectedValueOnce(new Error('Registry unreachable'));
    await expect(listAgentTools('agt-1')).rejects.toThrow('Registry unreachable');
  });
});
