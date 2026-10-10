/** dashboard/src/api/agent-test.ts — Test session real backend */
import { apiClient } from './client';
import type { TestSession } from '../types/agent-test';
export async function createTestSession(agentId: string): Promise<TestSession> {
  try { const res = await apiClient.post<any>(`/api/v1/agents/${agentId}/test`); return res.session || res; }
  catch { try { const res = await apiClient.post<any>(`/api/simulations`, { agent_id: agentId }); return { id: res.id || res.session_id, agent_id: agentId, state: 'IDLE', created_at: new Date().toISOString(), transcript: [] }; } catch (e:any) { if (e?.status===404 || e?.message?.includes('not configured')) { return { id: 'not-configured', agent_id: agentId, state: 'NOT_CONFIGURED', created_at: new Date().toISOString(), transcript: [] }; } throw e; } }
}
export async function postTestEvent(sessionId: string, event: string, payload?: Record<string, unknown>): Promise<TestSession> {
  const res = await apiClient.post<any>(`/api/v1/agent-tests/${sessionId}/events`, { event, payload });
  return res.session || res;
}
export async function getTestSession(sessionId: string): Promise<TestSession> {
  try { const res = await apiClient.get<any>(`/api/v1/agent-tests/${sessionId}`); return res.session || res; }
  catch (e:any) { if (e?.status===404) { return { id: sessionId, agent_id: '', state: 'NOT_CONFIGURED', created_at: new Date().toISOString(), transcript: [] }; } throw e; }
}
