/** dashboard/src/types/agent-filters.ts — Domain types */
export interface AgentFilters { id: string; name?: string; created_at?: string; }
export type AgentFiltersStatus = 'active'|'inactive'|'error';
export const AGENT_FILTERS_DEFAULT: AgentFilters = { id: 'default' };
