/**
 * dashboard/src/api/agent-search.ts
 *
 * Agent list search.
 *
 * There is no `/api/agent-search` endpoint. Searching a list the client has
 * already fetched is a client-side concern, and the previous version of this
 * file pretended otherwise by calling a 404 and returning `[]`.
 *
 * Matching is deliberately conservative: an agent with no name never matches a
 * non-empty query, and matching is case-insensitive substring matching over a
 * fixed set of indexed fields — no ranking is invented.
 */

import type { DurableAgentRecord } from './types/agent';

/** The fields search looks at. Adding a field here is the only way to widen it. */
const SEARCHABLE_FIELDS = ['name', 'description', 'id', 'external_key'] as const;

export interface AgentSearchOptions {
  caseSensitive?: boolean;
  maxResults?: number;
}

export function searchAgents<T extends DurableAgentRecord>(
  agents: T[],
  query: string,
  options: AgentSearchOptions = {},
): T[] {
  const trimmed = (query ?? '').trim();
  if (!trimmed) return agents;

  const needle = options.caseSensitive ? trimmed : trimmed.toLowerCase();
  const results = agents.filter((agent) => {
    const record = agent as unknown as Record<string, unknown>;
    return SEARCHABLE_FIELDS.some((field) => {
      const value = record[field];
      if (value === undefined || value === null) return false;
      const haystack = options.caseSensitive ? String(value) : String(value).toLowerCase();
      return haystack.includes(needle);
    });
  });

  return typeof options.maxResults === 'number' ? results.slice(0, options.maxResults) : results;
}

/** True when a query is specific enough to be worth showing "no results". */
export function isSearchActive(query: string): boolean {
  return (query ?? '').trim().length > 0;
}
