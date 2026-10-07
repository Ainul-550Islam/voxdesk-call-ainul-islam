/** dashboard/src/api/agent-security.ts — Real backend API wrapper */
import { apiClient } from './client';
export async function getAgentSecurity(agentId: string): Promise<any> { try{ const res=await apiClient.get(`/api/agents/${agentId}/agent-security`); return res; } catch{ return null; } }
export async function listAgentSecurity(): Promise<any[]> { try{ const res=await apiClient.get(`/api/agent-security`); return res.items||[]; } catch{ return []; } }
