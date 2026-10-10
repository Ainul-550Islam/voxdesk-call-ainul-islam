/**
 * dashboard/src/api/agent-conversation.ts
 *
 * Agent conversation flow & call-handling configuration client, backed by
 * `GET /api/agents/{agent_id}/flow` (`app/api/agent_flow_routes.py`) and
 * `GET /api/agents` (`app/api/agent_routes.py`).
 */

import { apiClient } from './client';

export async function getAgentConversation(agentId: string): Promise<Record<string, unknown>> {
  return apiClient.get<Record<string, unknown>>(
    `/api/agents/${encodeURIComponent(agentId)}/flow`,
  );
}

export async function listAgentConversation(): Promise<Array<Record<string, unknown>>> {
  const res = await apiClient.get<unknown>('/api/agents');
  if (Array.isArray(res)) {
    return res as Array<Record<string, unknown>>;
  }
  const obj = res as {
    items?: Array<Record<string, unknown>>;
    agents?: Array<Record<string, unknown>>;
  } | null;
  return obj?.items ?? obj?.agents ?? [];
}
