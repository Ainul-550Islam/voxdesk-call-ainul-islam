/** dashboard/src/tests/agent-validation.test.tsx — Contract tests for agent-validation API client */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { apiClient } from '../api/client';
import {
  validateAgentConfig,
  getAgentValidation,
} from '../api/agent-validation';

describe('agent-validation API contract', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('calls POST /api/v1/agents/{id}/validate and returns typed issues', async () => {
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValueOnce({
      valid: false,
      errors: [{ field: 'voice.voice_id', code: 'missing', message: 'Voice ID required' }],
      warnings: [],
      checked_at: '2026-10-09T00:00:00Z',
    });

    const result = await validateAgentConfig('agt-1');
    expect(result.valid).toBe(false);
    expect(result.errors[0].field).toBe('voice.voice_id');
    expect(postSpy).toHaveBeenCalledWith('/api/v1/agents/agt-1/validate', {});
  });

  it('propagates endpoint errors instead of returning null', async () => {
    vi.spyOn(apiClient, 'post').mockRejectedValueOnce(new Error('Validation service error'));
    await expect(getAgentValidation('agt-1')).rejects.toThrow('Validation service error');
  });
});
