/** dashboard/src/types/agent-test-event.ts — Domain types */
export interface AgentTestEvent { id: string; name?: string; created_at?: string; }
export type AgentTestEventStatus = 'active'|'inactive'|'error';
export const AGENT_TEST_EVENT_DEFAULT: AgentTestEvent = { id: 'default' };
