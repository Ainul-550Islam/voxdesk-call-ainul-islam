/** dashboard/src/types/agent-tool.ts — Domain types */
export interface AgentTool { id: string; name?: string; created_at?: string; }
export type AgentToolStatus = 'active'|'inactive'|'error';
export const AGENT_TOOL_DEFAULT: AgentTool = { id: 'default' };
