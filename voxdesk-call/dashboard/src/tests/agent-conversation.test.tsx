/** dashboard/src/tests/agent-conversation.test.tsx — Contract tests for agent-conversation API client */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { apiClient } from '../api/client';
import {
  getAgentConversation,
  listAgentConversation,
} from '../api/agent-conversation';

describe('agent-conversation API contract', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('fetches agent flow from /api/agents/{id}/flow and lists agents from /api/agents', async () => {
    const getSpy = vi
      .spyOn(apiClient, 'get')
      .mockResolvedValueOnce({ agent_id: 'agt-1', mode: 'flow', flow: { nodes: [] } })
      .mockResolvedValueOnce({ items: [{ id: 'agt-1', name: 'Support Flow' }] });

    const flow = await getAgentConversation('agt-1');
    expect(flow.agent_id).toBe('agt-1');
    expect(getSpy).toHaveBeenCalledWith('/api/agents/agt-1/flow');

    const items = await listAgentConversation();
    expect(items).toHaveLength(1);
    expect(getSpy).toHaveBeenCalledWith('/api/agents');
  });

  it('propagates errors instead of returning null or []', async () => {
    vi.spyOn(apiClient, 'get').mockRejectedValueOnce(new Error('404 Not Found'));
    await expect(getAgentConversation('missing')).rejects.toThrow('404 Not Found');
  });
});
