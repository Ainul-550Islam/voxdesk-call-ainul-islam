/** dashboard/src/types/agent-test-transcript.ts — Domain types */
export interface AgentTestTranscript { id: string; name?: string; created_at?: string; }
export type AgentTestTranscriptStatus = 'active'|'inactive'|'error';
export const AGENT_TEST_TRANSCRIPT_DEFAULT: AgentTestTranscript = { id: 'default' };
