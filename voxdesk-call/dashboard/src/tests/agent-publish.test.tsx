/** dashboard/src/tests/agent-publish.test.tsx — Contract tests for agent-publish API client */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { apiClient } from '../api/client';
import {
  getAgentPublish,
  listAgentPublish,
  publishAgentBuilder,
} from '../api/agent-publish';

describe('agent-publish API contract', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('queries /api/agents/{id}/environments and /api/v1/agents/{id}/versions', async () => {
    const getSpy = vi
      .spyOn(apiClient, 'get')
      .mockResolvedValueOnce({ staging: 1, production: 2 })
      .mockResolvedValueOnce([{ version: 2, status: 'published' }]);

    const envs = await getAgentPublish('agt-1');
    expect(envs.production).toBe(2);
    expect(getSpy).toHaveBeenCalledWith('/api/agents/agt-1/environments');

    const versions = await listAgentPublish('agt-1');
    expect(versions).toHaveLength(1);
    expect(getSpy).toHaveBeenCalledWith('/api/v1/agents/agt-1/versions');
  });

  it('publishes builder version and propagates failures', async () => {
    const postSpy = vi
      .spyOn(apiClient, 'post')
      .mockResolvedValueOnce({ version: 3, is_active: true })
      .mockRejectedValueOnce(new Error('Validation gate failed'));

    const res = await publishAgentBuilder('agt-1', { changelog: 'Ship v3' });
    expect(res.version).toBe(3);
    expect(postSpy).toHaveBeenCalledWith('/api/v1/agents/agt-1/publish', {
      changelog: 'Ship v3',
      release_notes: 'Ship v3',
      environment: 'production',
      environment_id: undefined,
    });

    await expect(publishAgentBuilder('agt-1')).rejects.toThrow('Validation gate failed');
  });
});
