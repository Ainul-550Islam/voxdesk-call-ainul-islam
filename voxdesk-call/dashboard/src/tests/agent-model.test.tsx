/** dashboard/src/tests/agent-model.test.tsx — Contract tests for agent-models API client */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { apiClient } from '../api/client';
import { getModelProviders, getAgentModels } from '../api/agent-models';

describe('agent-models API contract', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('fetches LLM catalog from GET /api/agents/models', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({
      providers: [{ id: 'openai', label: 'OpenAI', selectable: true }],
      presets: [{ preset: 'balanced', provider: 'openai', model: 'gpt-4o' }],
      default_provider: 'openai',
      default_preset: 'balanced',
    });

    const cat = await getModelProviders();
    expect(cat.default_provider).toBe('openai');
    expect(getSpy).toHaveBeenCalledWith('/api/agents/models');

    const perAgent = await getAgentModels('agt-1');
    expect(perAgent.presets[0].model).toBe('gpt-4o');
  });

  it('propagates errors from GET /api/agents/models', async () => {
    vi.spyOn(apiClient, 'get').mockRejectedValueOnce(new Error('Catalog offline'));
    await expect(getModelProviders()).rejects.toThrow('Catalog offline');
  });
});
