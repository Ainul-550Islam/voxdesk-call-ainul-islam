/**
 * dashboard/src/hooks/useAgentSort.ts
 *
 * Client-side sorting over an already-loaded agent list.
 *
 * There is no `/api/agent-sort` endpoint. The previous version of this hook
 * set `data = { agentId }` and never sorted anything, so clicking a column
 * header changed nothing.
 *
 * Sorting never mutates the caller's array, and records missing the sort key
 * sort last in both directions rather than being treated as an empty value
 * that outranks real data.
 */

import { useCallback, useMemo, useState } from 'react';
import {
  AGENT_SORT_DEFAULT,
  sortAgents,
  type AgentSortDirection,
  type AgentSortKey,
  type AgentSortState,
} from '../api/agent-sort';
import type { DurableAgentRecord } from '../api/types/agent';

export interface UseAgentSortResult<T extends DurableAgentRecord> {
  sort: AgentSortState;
  setSort: (key: AgentSortKey, direction?: AgentSortDirection) => void;
  /** Same key toggles direction; a new key starts descending. */
  toggle: (key: AgentSortKey) => void;
  reset: () => void;
  sorted: T[];
}

export function useAgentSort<T extends DurableAgentRecord>(
  agents: T[],
  initial: AgentSortState = AGENT_SORT_DEFAULT,
): UseAgentSortResult<T> {
  const [sort, setSortState] = useState<AgentSortState>(initial);

  const sorted = useMemo(() => sortAgents(agents, sort), [agents, sort]);

  const setSort = useCallback((key: AgentSortKey, direction?: AgentSortDirection) => {
    setSortState((current) => ({
      key,
      direction: direction ?? current.direction,
    }));
  }, []);

  const toggle = useCallback((key: AgentSortKey) => {
    setSortState((current) =>
      current.key === key
        ? { key, direction: current.direction === 'asc' ? 'desc' : 'asc' }
        : { key, direction: 'desc' },
    );
  }, []);

  const reset = useCallback(() => setSortState(initial), [initial]);

  return { sort, setSort, toggle, reset, sorted };
}

export default useAgentSort;
export type { AgentSortDirection, AgentSortKey, AgentSortState };
