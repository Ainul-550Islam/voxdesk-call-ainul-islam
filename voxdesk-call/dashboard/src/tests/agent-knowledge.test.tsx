/** dashboard/src/tests/agent-knowledge.test.tsx — Contract tests for agent-knowledge API client */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { apiClient } from '../api/client';
import {
  listAgentKnowledge,
  getAgentKnowledge,
} from '../api/agent-knowledge';

describe('agent-knowledge API contract', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('calls /api/knowledge/documents and returns typed document list', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValueOnce({
      documents: [{ id: 'doc-1', title: 'FAQ.pdf', status: 'ready' }],
      total: 1,
      limits: {},
    });

    const res = await listAgentKnowledge({ status: 'ready' });
    expect(res.total).toBe(1);
    expect(res.documents[0].title).toBe('FAQ.pdf');
    expect(getSpy).toHaveBeenCalledWith('/api/knowledge/documents?status=ready');
  });

  it('fetches single document and propagates backend errors', async () => {
    const getSpy = vi
      .spyOn(apiClient, 'get')
      .mockResolvedValueOnce({ id: 'doc-1', title: 'Policy.md', status: 'ready' })
      .mockRejectedValueOnce(new Error('Document not found'));

    const doc = await getAgentKnowledge('doc-1');
    expect(doc.id).toBe('doc-1');
    expect(getSpy).toHaveBeenCalledWith('/api/knowledge/documents/doc-1');

    await expect(getAgentKnowledge('missing')).rejects.toThrow('Document not found');
  });
});
