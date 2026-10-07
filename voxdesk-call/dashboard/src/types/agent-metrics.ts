/** dashboard/src/types/agent-metrics.ts — Domain types */
export interface AgentMetrics { id: string; name?: string; created_at?: string; }
export type AgentMetricsStatus = 'active'|'inactive'|'error';
export const AGENT_METRICS_DEFAULT: AgentMetrics = { id: 'default' };
