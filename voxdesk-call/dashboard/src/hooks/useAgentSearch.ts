/**
 * dashboard/src/hooks/useAgentSearch.ts
 *
 * Client-side search over an already-loaded agent list.
 *
 * There is no `/api/agent-search` endpoint, and inventing one is worse than
 * not searching: the previous version of this hook set `data = { agentId }`
 * and never filtered anything, so a user typing in the search box saw the
 * unfiltered list and no indication that search did nothing.
 *
 * This hook filters the list it is given, in memory, and reports whether a
 * query is active so the UI can distinguish "no agents" from "no matches".
 */

import { useCallback, useMemo, useState } from 'react';
import { isSearchActive, searchAgents, type AgentSearchOptions } from '../api/agent-search';
import type { DurableAgentRecord } from '../api/types/agent';

export interface UseAgentSearchResult<T extends DurableAgentRecord> {
  query: string;
  setQuery: (value: string) => void;
  clear: () => void;
  results: T[];
  active: boolean;
  matchCount: number;
}

export function useAgentSearch<T extends DurableAgentRecord>(
  agents: T[],
  options: AgentSearchOptions = {},
): UseAgentSearchResult<T> {
  const [query, setQuery] = useState('');

  const results = useMemo(
    () => searchAgents(agents, query, options),
    // `options` is an object literal at most call sites; depending on its
    // fields keeps the memo stable for the common case.
    [agents, query, options.caseSensitive, options.maxResults],
  );

  const clear = useCallback(() => setQuery(''), []);

  return {
    query,
    setQuery,
    clear,
    results,
    active: isSearchActive(query),
    matchCount: results.length,
  };
}

export default useAgentSearch;
