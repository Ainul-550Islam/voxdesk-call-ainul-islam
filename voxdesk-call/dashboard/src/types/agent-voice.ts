/** dashboard/src/types/agent-voice.ts — Domain types */
export interface AgentVoice { id: string; name?: string; created_at?: string; }
export type AgentVoiceStatus = 'active'|'inactive'|'error';
export const AGENT_VOICE_DEFAULT: AgentVoice = { id: 'default' };
