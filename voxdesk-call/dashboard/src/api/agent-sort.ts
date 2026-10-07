/**
 * dashboard/src/api/agent-sort.ts
 *
 * Agent list sorting.
 *
 * There is no `/api/agent-sort` endpoint; ordering a list the client has
 * already fetched is a client-side concern. (No such file existed before — it
 * is introduced together with `useAgentSort`, which previously returned a
 * placeholder object.)
 *
 * Rules:
 * - Sorting never mutates the caller's array.
 * - Records missing the sort key sort last, in both directions, rather than
 *   being silently treated as an empty string that outranks real values.
 * - Unknown sort keys fall back to `updated_at` descending, never to an
 *   undefined comparator that would leave the order arbitrary.
 */

import type { DurableAgentRecord } from './types/agent';

export type AgentSortKey = 'name' | 'updated_at' | 'created_at' | 'status' | 'version';
export type AgentSortDirection = 'asc' | 'desc';

export interface AgentSortState {
  key: AgentSortKey;
  direction: AgentSortDirection;
}

export const AGENT_SORT_DEFAULT: AgentSortState = {
  key: 'updated_at',
  direction: 'desc',
};

const FALLBACK: AgentSortState = AGENT_SORT_DEFAULT;

function keyValue(agent: DurableAgentRecord, key: AgentSortKey): string | number | null {
  const record = agent as unknown as Record<string, unknown>;
  switch (key) {
    case 'name':
      return typeof record.name === 'string' ? record.name.toLowerCase() : null;
    case 'status':
      return typeof record.status === 'string' ? record.status.toUpperCase() : null;
    case 'version': {
      const raw = record.published_version_number ?? record.version ?? record.active_version;
      const value = typeof raw === 'string' ? Number(raw) : raw;
      return typeof value === 'number' && Number.isFinite(value) ? value : null;
    }
    case 'created_at':
      return typeof record.created_at === 'string' ? record.created_at : null;
    case 'updated_at':
    default:
      return typeof record.updated_at === 'string' ? record.updated_at : null;
  }
}

export function sortAgents<T extends DurableAgentRecord>(
  agents: T[],
  state: AgentSortState = AGENT_SORT_DEFAULT,
): T[] {
  const active = state && state.key ? state : FALLBACK;
  const direction = active.direction === 'asc' ? 1 : -1;

  return [...agents].sort((left, right) => {
    const a = keyValue(left, active.key);
    const b = keyValue(right, active.key);
    if (a === null && b === null) return 0;
    if (a === null) return 1; // missing values always sort last
    if (b === null) return -1;
    if (a === b) return 0;
    return (a < b ? -1 : 1) * direction;
  });
}
