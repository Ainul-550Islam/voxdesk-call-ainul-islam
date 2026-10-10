/** dashboard/src/tests/agent-security.test.tsx — Contract tests for agent-security API client */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { apiClient } from '../api/client';
import { getAgentSecurity, listAgentSecurity } from '../api/agent-security';

describe('agent-security API contract', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('reads agent security config from /api/v1/agents/{id}/builder and events from /api/security/events', async () => {
    const getSpy = vi
      .spyOn(apiClient, 'get')
      .mockResolvedValueOnce({
        agent_id: 'agt-1',
        config: { security: { pii_redaction: true } },
      })
      .mockResolvedValueOnce([{ id: 'evt-1', action: 'session.login' }]);

    const sec = await getAgentSecurity('agt-1');
    expect(sec.agent_id).toBe('agt-1');
    expect((sec.security as any).pii_redaction).toBe(true);
    expect(getSpy).toHaveBeenCalledWith('/api/v1/agents/agt-1/builder');

    const events = await listAgentSecurity();
    expect(events).toHaveLength(1);
    expect(getSpy).toHaveBeenCalledWith('/api/security/events');
  });

  it('propagates backend errors instead of swallowing into null', async () => {
    vi.spyOn(apiClient, 'get').mockRejectedValueOnce(new Error('403 Forbidden'));
    await expect(getAgentSecurity('agt-1')).rejects.toThrow('403 Forbidden');
  });
});
