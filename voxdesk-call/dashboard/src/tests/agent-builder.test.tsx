/** dashboard/src/tests/agent-builder.test.tsx — Contract tests for agent-builder API client */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { apiClient } from '../api/client';
import {
  getBuilderConfig,
  updateBuilderConfig,
  validateAgent,
  getVersions,
} from '../api/agent-builder';

describe('agent-builder API contract', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('fetches and patches builder config with If-Match ETag header', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValueOnce({
      agent_id: 'agt-1',
      draft_etag: 'W/"v1"',
      config: {
        identity: { name: 'Clinic Bot' },
        voice: { voice_id: 'rachel', language: 'en-US' },
        model: { model_name: 'gpt-4o', system_prompt: 'Be helpful.' },
      },
    });
    const cfg = await getBuilderConfig('agt-1');
    expect(cfg.name).toBe('Clinic Bot');
    expect(cfg.etag).toBe('W/"v1"');

    const patchSpy = vi.spyOn(apiClient, 'patch').mockResolvedValueOnce({
      agent_id: 'agt-1',
      draft_etag: 'W/"v2"',
      config: { identity: { name: 'Updated Clinic Bot' } },
    });
    const updated = await updateBuilderConfig('agt-1', { name: 'Updated Clinic Bot' }, 'W/"v1"');
    expect(updated.etag).toBe('W/"v2"');
    expect(patchSpy).toHaveBeenCalledWith(
      '/api/v1/agents/agt-1/builder',
      expect.objectContaining({ expected_etag: 'W/"v1"' }),
      { headers: { 'If-Match': 'W/"v1"' } },
    );
  });

  it('validates config and lists versions without swallowing errors', async () => {
    vi.spyOn(apiClient, 'post').mockResolvedValueOnce({
      valid: true,
      errors: [],
      warnings: [],
      checked_at: '2026-10-09T00:00:00Z',
    });
    const res = await validateAgent('agt-1');
    expect(res.valid).toBe(true);

    vi.spyOn(apiClient, 'get').mockRejectedValueOnce(new Error('Version service unavailable'));
    await expect(getVersions('agt-1')).rejects.toThrow('Version service unavailable');
  });
});
