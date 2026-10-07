import { client } from './client';
import type { VoiceDemoSession, VoiceDemoEvent, VoiceDemoEventResponse, VoiceDemoSessionState } from '../types/voice';
export async function createVoiceDemoSession(tenantId?: string, metadata: Record<string,unknown>={}): Promise<VoiceDemoSession> {
  return client.post<VoiceDemoSession>('/api/v1/public/voice-demo/session',{tenant_id:tenantId,metadata});
}
export async function postVoiceDemoEvent(sessionId:string, event:VoiceDemoEvent): Promise<VoiceDemoEventResponse> {
  return client.post<VoiceDemoEventResponse>(`/api/v1/public/voice-demo/session/${encodeURIComponent(sessionId)}/event`,event);
}
export async function getVoiceDemoSession(sessionId:string): Promise<VoiceDemoSessionState> {
  return client.get<VoiceDemoSessionState>(`/api/v1/public/voice-demo/session/${encodeURIComponent(sessionId)}`);
}
