/** dashboard/src/types/agent-template.ts — Domain types */
export interface AgentTemplate { id: string; name?: string; created_at?: string; }
export type AgentTemplateStatus = 'active'|'inactive'|'error';
export const AGENT_TEMPLATE_DEFAULT: AgentTemplate = { id: 'default' };
