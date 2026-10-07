/** dashboard/src/types/agent-validation.ts — Domain types */
export interface AgentValidation { id: string; name?: string; created_at?: string; }
export type AgentValidationStatus = 'active'|'inactive'|'error';
export const AGENT_VALIDATION_DEFAULT: AgentValidation = { id: 'default' };
