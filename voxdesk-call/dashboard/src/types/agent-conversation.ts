/** dashboard/src/types/agent-conversation.ts — Domain types */
export interface AgentConversation { id: string; name?: string; created_at?: string; }
export type AgentConversationStatus = 'active'|'inactive'|'error';
export const AGENT_CONVERSATION_DEFAULT: AgentConversation = { id: 'default' };
