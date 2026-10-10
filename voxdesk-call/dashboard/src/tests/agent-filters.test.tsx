/** dashboard/src/tests/agent-filters.test.tsx — Unit tests for client-side agent filters */
import { describe, it, expect } from 'vitest';
import {
  AGENT_FILTER_DEFAULT,
  applyAgentFilters,
  availableAgentFilterValues,
} from '../api/agent-filters';

const SAMPLE_AGENTS = [
  { id: '1', name: 'Voice A', status: 'PUBLISHED', type: 'VOICE', language: 'en-US' },
  { id: '2', name: 'Chat B', status: 'DRAFT', type: 'CHAT', language: 'es-ES' },
] as any[];

describe('agent-filters pure helpers', () => {
  it('filters agents by status, type, and language without network calls', () => {
    const published = applyAgentFilters(SAMPLE_AGENTS, {
      ...AGENT_FILTER_DEFAULT,
      status: 'PUBLISHED',
    });
    expect(published).toHaveLength(1);
    expect(published[0].id).toBe('1');

    const spanish = applyAgentFilters(SAMPLE_AGENTS, {
      ...AGENT_FILTER_DEFAULT,
      language: 'es-ES',
    });
    expect(spanish).toHaveLength(1);
    expect(spanish[0].id).toBe('2');
  });

  it('extracts distinct filter values from agent list', () => {
    const facet = availableAgentFilterValues(SAMPLE_AGENTS);
    expect(facet.statuses).toEqual(['DRAFT', 'PUBLISHED']);
    expect(facet.agentTypes).toEqual(['CHAT', 'VOICE']);
    expect(facet.languages).toEqual(['en-US', 'es-ES']);
  });
});
