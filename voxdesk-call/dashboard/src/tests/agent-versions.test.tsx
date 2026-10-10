/** dashboard/src/tests/agent-versions.test.tsx — Contract tests for agent-versions API client */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { apiClient } from '../api/client';
import {
  getAgentVersions,
  fetchAgentVersion,
  diffAgentVersions,
  rollbackToAgentVersion,
} from '../api/agent-versions';

describe('agent-versions API contract', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('fetches version list, snapshot, diff, and rollback via /api/agents/{id}/versions', async () => {
    const getSpy = vi
      .spyOn(apiClient, 'get')
      .mockResolvedValueOnce([{ version: 2, status: 'published' }])
      .mockResolvedValueOnce({ version: 1, status: 'superseded' })
      .mockResolvedValueOnce({ from_version: 1, to_version: 2, total_changes: 1, diffs: [] });
    const postSpy = vi
      .spyOn(apiClient, 'post')
      .mockResolvedValueOnce({ version: 3, is_rollback: true });

    const list = await getAgentVersions('agt-1');
    expect(list).toHaveLength(1);
    expect(getSpy).toHaveBeenCalledWith('/api/agents/agt-1/versions');

    const snap = await fetchAgentVersion('agt-1', 1);
    expect(snap.version).toBe(1);

    const diff = await diffAgentVersions('agt-1', 1, 2);
    expect(diff.total_changes).toBe(1);

    const rb = await rollbackToAgentVersion('agt-1', { target_version: 1, reason: 'revert' });
    expect(rb.is_rollback).toBe(true);
    expect(postSpy).toHaveBeenCalledWith('/api/agents/agt-1/rollback', {
      target_version: 1,
      reason: 'revert',
    });
  });

  it('propagates errors when version history endpoint fails', async () => {
    vi.spyOn(apiClient, 'get').mockRejectedValueOnce(new Error('Database timeout'));
    await expect(getAgentVersions('agt-1')).rejects.toThrow('Database timeout');
  });
});
