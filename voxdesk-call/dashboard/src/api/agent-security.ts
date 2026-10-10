/**
 * dashboard/src/api/agent-security.ts
 *
 * Agent security configuration & tenant security events client, backed by
 * `GET /api/v1/agents/{agent_id}/builder` (`app/api/agent_builder_routes.py`)
 * and `GET /api/security/events` (`app/api/security_session_routes.py`).
 */

import { apiClient } from './client';

export async function getAgentSecurity(agentId: string): Promise<Record<string, unknown>> {
  const res = await apiClient.get<Record<string, unknown>>(
    `/api/v1/agents/${encodeURIComponent(agentId)}/builder`,
  );
  const cfg = (res?.config as Record<string, unknown> | undefined) ?? res ?? {};
  return {
    agent_id: String(res?.agent_id ?? res?.id ?? agentId),
    security: (cfg.security as Record<string, unknown> | undefined) ?? {},
  };
}

export async function listAgentSecurity(): Promise<Array<Record<string, unknown>>> {
  const res = await apiClient.get<unknown>('/api/security/events');
  if (Array.isArray(res)) {
    return res as Array<Record<string, unknown>>;
  }
  const obj = res as {
    items?: Array<Record<string, unknown>>;
    events?: Array<Record<string, unknown>>;
  } | null;
  return obj?.items ?? obj?.events ?? [];
}
