/** dashboard/src/tests/agent-voice.test.tsx — Contract tests for agent-voices API client */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { apiClient } from '../api/client';
import { getVoiceProviders, getAgentVoices } from '../api/agent-voices';

describe('agent-voices API contract', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('fetches voice catalog from GET /api/agents/voices', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({
      providers: [{ id: 'elevenlabs', label: 'ElevenLabs', selectable: true }],
      voices: [{ voice_id: 'rachel', provider: 'elevenlabs', label: 'Rachel' }],
      default_provider: 'elevenlabs',
      default_voice_id: 'rachel',
      voice_library_fetched: false,
    });

    const cat = await getVoiceProviders();
    expect(cat.default_voice_id).toBe('rachel');
    expect(getSpy).toHaveBeenCalledWith('/api/agents/voices');

    const perAgent = await getAgentVoices('agt-1');
    expect(perAgent.voices[0].voice_id).toBe('rachel');
  });

  it('propagates errors from GET /api/agents/voices', async () => {
    vi.spyOn(apiClient, 'get').mockRejectedValueOnce(new Error('Voice catalog error'));
    await expect(getVoiceProviders()).rejects.toThrow('Voice catalog error');
  });
});
