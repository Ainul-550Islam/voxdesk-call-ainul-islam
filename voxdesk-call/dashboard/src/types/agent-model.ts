/** dashboard/src/types/agent-model.ts — Domain types */
export interface AgentModel { id: string; name?: string; created_at?: string; }
export type AgentModelStatus = 'active'|'inactive'|'error';
export const AGENT_MODEL_DEFAULT: AgentModel = { id: 'default' };
