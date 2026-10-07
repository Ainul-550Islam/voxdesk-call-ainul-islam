/** dashboard/src/types/agent-security.ts — Domain types */
export interface AgentSecurity { id: string; name?: string; created_at?: string; }
export type AgentSecurityStatus = 'active'|'inactive'|'error';
export const AGENT_SECURITY_DEFAULT: AgentSecurity = { id: 'default' };
