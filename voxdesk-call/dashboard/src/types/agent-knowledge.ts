/** dashboard/src/types/agent-knowledge.ts — Domain types */
export interface AgentKnowledge { id: string; name?: string; created_at?: string; }
export type AgentKnowledgeStatus = 'active'|'inactive'|'error';
export const AGENT_KNOWLEDGE_DEFAULT: AgentKnowledge = { id: 'default' };
