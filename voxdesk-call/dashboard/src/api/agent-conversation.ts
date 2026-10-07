/** dashboard/src/api/agent-conversation.ts — Real backend API wrapper */
import { apiClient } from './client';
export async function getAgentConversation(agentId: string): Promise<any> { try{ const res=await apiClient.get(`/api/agents/${agentId}/agent-conversation`); return res; } catch{ return null; } }
export async function listAgentConversation(): Promise<any[]> { try{ const res=await apiClient.get(`/api/agent-conversation`); return res.items||[]; } catch{ return []; } }
