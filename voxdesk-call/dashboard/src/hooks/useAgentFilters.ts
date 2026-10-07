/**
 * dashboard/src/hooks/useAgentFilters.ts
 *
 * Client-side filtering over an already-loaded agent list.
 *
 * There is no `/api/agent-filters` endpoint. The previous version of this hook
 * set `data = { agentId }` and never filtered anything, so the status/type
 * dropdowns changed the URL and nothing else.
 *
 * Available filter values are derived from the loaded list rather than from a
 * hardcoded enum, so a new agent type can never appear in the data without
 * also becoming selectable.
 */

import { useCallback, useMemo, useState } from 'react';
import {
  AGENT_FILTER_DEFAULT,
  applyAgentFilters,
  availableAgentFilterValues,
  countActiveFilters,
  type AgentFilterState,
} from '../api/agent-filters';
import type { DurableAgentRecord } from '../api/types/agent';

export interface UseAgentFiltersResult<T extends DurableAgentRecord> {
  filters: AgentFilterState;
  setFilter: (key: keyof AgentFilterState, value: string) => void;
  reset: () => void;
  filtered: T[];
  activeCount: number;
  options: {
    statuses: string[];
    agentTypes: string[];
    languages: string[];
  };
}

export function useAgentFilters<T extends DurableAgentRecord>(agents: T[]): UseAgentFiltersResult<T> {
  const [filters, setFilters] = useState<AgentFilterState>(AGENT_FILTER_DEFAULT);

  const filtered = useMemo(() => applyAgentFilters(agents, filters), [agents, filters]);
  const options = useMemo(() => availableAgentFilterValues(agents), [agents]);

  const setFilter = useCallback((key: keyof AgentFilterState, value: string) => {
    setFilters((current) => ({ ...current, [key]: value }));
  }, []);

  const reset = useCallback(() => setFilters(AGENT_FILTER_DEFAULT), []);

  return {
    filters,
    setFilter,
    reset,
    filtered,
    activeCount: countActiveFilters(filters),
    options,
  };
}

export default useAgentFilters;
