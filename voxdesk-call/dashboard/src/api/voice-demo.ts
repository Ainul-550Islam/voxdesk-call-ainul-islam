import { client } from './client';
import type {
  VoiceDemoSession,
  VoiceDemoEvent,
  VoiceDemoEventResponse,
  VoiceDemoSessionState,
} from '../types/voice';

function tokenHeaders(sessionToken?: string): Record<string, string> | undefined {
  return sessionToken ? { 'X-Demo-Session-Token': sessionToken } : undefined;
}

export async function createVoiceDemoSession(
  tenantId?: string,
  metadata: Record<string, unknown> = {},
): Promise<VoiceDemoSession> {
  return client.post<VoiceDemoSession>(
    '/api/v1/public/voice-demo/session',
    { tenant_id: tenantId, metadata },
    { authenticated: false },
  );
}

export async function postVoiceDemoEvent(
  sessionId: string,
  event: VoiceDemoEvent,
  sessionToken?: string,
): Promise<VoiceDemoEventResponse> {
  const token = sessionToken || event.session_token;
  return client.post<VoiceDemoEventResponse>(
    `/api/v1/public/voice-demo/session/${encodeURIComponent(sessionId)}/event`,
    event,
    { authenticated: false, headers: tokenHeaders(token) },
  );
}

export async function postVoiceDemoTurn(
  sessionId: string,
  text: string,
  sessionToken: string,
): Promise<VoiceDemoEventResponse> {
  return client.post<VoiceDemoEventResponse>(
    `/api/v1/public/voice-demo/session/${encodeURIComponent(sessionId)}/turn`,
    { text, session_token: sessionToken },
    { authenticated: false, headers: tokenHeaders(sessionToken) },
  );
}

export async function endVoiceDemoSession(
  sessionId: string,
  sessionToken: string,
): Promise<VoiceDemoEventResponse> {
  return client.post<VoiceDemoEventResponse>(
    `/api/v1/public/voice-demo/session/${encodeURIComponent(sessionId)}/end`,
    {},
    { authenticated: false, headers: tokenHeaders(sessionToken) },
  );
}

export async function getVoiceDemoSession(
  sessionId: string,
  sessionToken?: string,
): Promise<VoiceDemoSessionState> {
  return client.get<VoiceDemoSessionState>(
    `/api/v1/public/voice-demo/session/${encodeURIComponent(sessionId)}`,
    { authenticated: false, headers: tokenHeaders(sessionToken) },
  );
}
