/**
 * dashboard/src/api/agent-filters.ts
 *
 * Agent list filtering.
 *
 * There is no `/api/agent-filters` endpoint — filtering a list the client has
 * already fetched is a client-side concern, and the previous version of this
 * file pretended otherwise by calling a 404 and returning `[]`.
 *
 * These are pure functions over an already-loaded agent list: no network, no
 * hidden state, no fabrication. Values that cannot be determined are treated
 * as "does not match the filter" rather than as "matches everything".
 */

import type { DurableAgentRecord } from './types/agent';

export interface AgentFilterState {
  status: string; // 'all' | 'DRAFT' | 'PUBLISHED' | 'ARCHIVED' | ...
  agentType: string; // 'all' | 'VOICE' | 'CHAT' | ...
  language: string; // 'all' | a BCP-47 tag
}

export const AGENT_FILTER_DEFAULT: AgentFilterState = {
  status: 'all',
  agentType: 'all',
  language: 'all',
};

function matches(value: unknown, filter: string): boolean {
  if (filter === 'all') return true;
  return String(value ?? '').toUpperCase() === filter.toUpperCase();
}

export function applyAgentFilters<T extends DurableAgentRecord>(
  agents: T[],
  filters: AgentFilterState,
): T[] {
  return agents.filter(
    (agent) =>
      matches(agent.status, filters.status) &&
      matches(agent.type, filters.agentType) &&
      matches(agent.language, filters.language),
  );
}

/** The distinct values each filter can offer, derived from the real list. */
export function availableAgentFilterValues<T extends DurableAgentRecord>(agents: T[]): {
  statuses: string[];
  agentTypes: string[];
  languages: string[];
} {
  const statuses = new Set<string>();
  const types = new Set<string>();
  const languages = new Set<string>();
  for (const agent of agents) {
    if (agent.status) statuses.add(String(agent.status).toUpperCase());
    if (agent.type) types.add(String(agent.type).toUpperCase());
    if (agent.language) languages.add(String(agent.language));
  }
  return {
    statuses: [...statuses].sort(),
    agentTypes: [...types].sort(),
    languages: [...languages].sort(),
  };
}

export function countActiveFilters(filters: AgentFilterState): number {
  return Object.values(filters).filter((value) => value !== 'all').length;
}
