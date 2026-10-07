import { apiClient } from './client';
import type {
  ChatAgentConfig,
  ChatAgentRecord,
  ChatAgentValidationResponse,
  ChatAgentVersionRecord,
  ChatMessageRecord,
  ChatSessionRecord,
  ChatTurnResult,
} from './types/chat-agent';

export async function listChatAgents(params?: {
  status?: string;
  limit?: number;
  offset?: number;
}): Promise<ChatAgentRecord[]> {
  const qs = new URLSearchParams();
  if (params?.status && params.status !== 'all') qs.set('status', params.status);
  if (params?.limit) qs.set('limit', String(params.limit));
  if (params?.offset) qs.set('offset', String(params.offset));
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  const res = await apiClient.get<ChatAgentRecord[]>(`/api/chat-agents${suffix}`);
  return Array.isArray(res) ? res : [];
}

export async function getChatAgent(agentId: string): Promise<ChatAgentRecord> {
  return apiClient.get<ChatAgentRecord>(
    `/api/chat-agents/${encodeURIComponent(agentId)}`
  );
}

export async function createChatAgent(input: {
  name: string;
  description?: string;
  draft_config?: ChatAgentConfig;
}): Promise<ChatAgentRecord> {
  return apiClient.post<ChatAgentRecord>('/api/chat-agents', {
    name: input.name,
    description: input.description || '',
    draft_config: input.draft_config || {},
  });
}

export async function updateChatAgentDraft(
  agentId: string,
  input: {
    name?: string;
    description?: string;
    draft_config?: ChatAgentConfig;
  },
  etag?: string
): Promise<ChatAgentRecord> {
  const headers: Record<string, string> = {};
  if (etag) {
    headers['If-Match'] = etag;
  }
  return apiClient.patch<ChatAgentRecord>(
    `/api/chat-agents/${encodeURIComponent(agentId)}`,
    input,
    { headers }
  );
}

export async function validateChatAgentDraft(
  agentId: string
): Promise<ChatAgentValidationResponse> {
  return apiClient.post<ChatAgentValidationResponse>(
    `/api/chat-agents/${encodeURIComponent(agentId)}/validate`,
    {}
  );
}

export async function publishChatAgent(
  agentId: string,
  changeSummary = ''
): Promise<ChatAgentVersionRecord> {
  return apiClient.post<ChatAgentVersionRecord>(
    `/api/chat-agents/${encodeURIComponent(agentId)}/publish`,
    { change_summary: changeSummary }
  );
}

export async function listChatAgentVersions(
  agentId: string
): Promise<ChatAgentVersionRecord[]> {
  const res = await apiClient.get<ChatAgentVersionRecord[]>(
    `/api/chat-agents/${encodeURIComponent(agentId)}/versions`
  );
  return Array.isArray(res) ? res : [];
}

export async function getChatAgentVersion(
  agentId: string,
  versionNumber: number
): Promise<ChatAgentVersionRecord> {
  return apiClient.get<ChatAgentVersionRecord>(
    `/api/chat-agents/${encodeURIComponent(agentId)}/versions/${encodeURIComponent(
      String(versionNumber)
    )}`
  );
}

export async function rollbackChatAgentVersion(
  agentId: string,
  targetVersion: number,
  reason = ''
): Promise<ChatAgentVersionRecord> {
  return apiClient.post<ChatAgentVersionRecord>(
    `/api/chat-agents/${encodeURIComponent(agentId)}/rollback`,
    {
      target_version: targetVersion,
      reason,
    }
  );
}

export async function archiveChatAgent(agentId: string): Promise<ChatAgentRecord> {
  return apiClient.post<ChatAgentRecord>(
    `/api/chat-agents/${encodeURIComponent(agentId)}/archive`,
    {}
  );
}

export async function restoreChatAgent(agentId: string): Promise<ChatAgentRecord> {
  return apiClient.post<ChatAgentRecord>(
    `/api/chat-agents/${encodeURIComponent(agentId)}/restore`,
    {}
  );
}

// --------------------------------------------------- Chat Sessions & Messages

export async function createChatSession(
  agentId: string,
  input?: {
    contact_id?: string;
    contact_phone?: string;
    contact_name?: string;
    channel?: 'web' | 'sms' | 'whatsapp' | 'api';
    dynamic_variables?: Record<string, unknown>;
    metadata?: Record<string, unknown>;
  }
): Promise<ChatSessionRecord> {
  return apiClient.post<ChatSessionRecord>(
    `/api/chat-agents/${encodeURIComponent(agentId)}/sessions`,
    {
      contact_id: input?.contact_id,
      contact_phone: input?.contact_phone,
      contact_name: input?.contact_name,
      channel: input?.channel || 'web',
      dynamic_variables: input?.dynamic_variables || {},
      metadata: input?.metadata || {},
    }
  );
}

export async function listChatSessions(params?: {
  chat_agent_id?: string;
  contact_id?: string;
  status?: string;
  limit?: number;
}): Promise<ChatSessionRecord[]> {
  const qs = new URLSearchParams();
  if (params?.chat_agent_id) qs.set('chat_agent_id', params.chat_agent_id);
  if (params?.contact_id) qs.set('contact_id', params.contact_id);
  if (params?.status) qs.set('status', params.status);
  if (params?.limit) qs.set('limit', String(params.limit));
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  const res = await apiClient.get<ChatSessionRecord[]>(`/api/chat-sessions${suffix}`);
  return Array.isArray(res) ? res : [];
}

export async function getChatSession(sessionId: string): Promise<ChatSessionRecord> {
  return apiClient.get<ChatSessionRecord>(
    `/api/chat-sessions/${encodeURIComponent(sessionId)}`
  );
}

export async function listChatMessages(
  sessionId: string
): Promise<ChatMessageRecord[]> {
  const res = await apiClient.get<ChatMessageRecord[]>(
    `/api/chat-sessions/${encodeURIComponent(sessionId)}/messages`
  );
  return Array.isArray(res) ? res : [];
}

export async function sendChatMessage(
  sessionId: string,
  input: {
    content: string;
    memory_updates?: Record<string, string>;
    metadata?: Record<string, unknown>;
  }
): Promise<ChatTurnResult> {
  return apiClient.post<ChatTurnResult>(
    `/api/chat-sessions/${encodeURIComponent(sessionId)}/messages`,
    {
      content: input.content,
      role: 'user',
      memory_updates: input.memory_updates || {},
      metadata: input.metadata || {},
    }
  );
}

export async function endChatSession(
  sessionId: string,
  status: 'completed' | 'escalated' | 'expired' = 'completed'
): Promise<ChatSessionRecord> {
  return apiClient.post<ChatSessionRecord>(
    `/api/chat-sessions/${encodeURIComponent(sessionId)}/end`,
    { status }
  );
}
