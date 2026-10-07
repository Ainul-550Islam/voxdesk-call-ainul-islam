/** dashboard/src/api/agent-publish.ts — Durable Agent Publish, Environment Promotion & Rollback API client */
import { apiClient } from './client';
import type {
  AgentVersionSnapshot,
  PublishAgentVersionInput,
} from './types/agent-version';

export async function getAgentPublish(agentId: string): Promise<any> {
  try {
    return await apiClient.get(`/api/agents/${encodeURIComponent(agentId)}/environments`);
  } catch {
    return null;
  }
}

export async function listAgentPublish(agentId?: string): Promise<AgentVersionSnapshot[]> {
  if (!agentId) return [];
  try {
    const res = await apiClient.get<any>(
      `/api/v1/agents/${encodeURIComponent(agentId)}/versions`
    );
    return Array.isArray(res) ? res : res?.items || res?.versions || [];
  } catch {
    return [];
  }
}

export async function publishAgentBuilder(
  agentId: string,
  input: PublishAgentVersionInput = {}
): Promise<any> {
  return apiClient.post(`/api/v1/agents/${encodeURIComponent(agentId)}/publish`, {
    changelog: input.changelog ?? input.release_notes ?? '',
    release_notes: input.release_notes ?? input.changelog ?? '',
    environment: input.environment ?? 'production',
    environment_id: input.environment_id,
  });
}

export async function fetchBuilderVersions(
  agentId: string
): Promise<AgentVersionSnapshot[]> {
  const res = await apiClient.get<any>(
    `/api/v1/agents/${encodeURIComponent(agentId)}/versions`
  );
  return Array.isArray(res) ? res : res?.versions || [];
}

export async function fetchBuilderVersion(
  agentId: string,
  versionNumber: number
): Promise<AgentVersionSnapshot> {
  return apiClient.get<AgentVersionSnapshot>(
    `/api/v1/agents/${encodeURIComponent(agentId)}/versions/${encodeURIComponent(String(versionNumber))}`
  );
}

export async function rollbackBuilderVersion(
  agentId: string,
  version: number,
  reason = ''
): Promise<AgentVersionSnapshot> {
  return apiClient.post<AgentVersionSnapshot>(
    `/api/v1/agents/${encodeURIComponent(agentId)}/rollback`,
    {
      version,
      reason,
    }
  );
}

export async function promoteAgentEnvironment(
  agentId: string,
  fromEnv: 'draft' | 'staging',
  toEnv: 'staging' | 'production',
  changelog = ''
): Promise<any> {
  return apiClient.post(`/api/agents/${encodeURIComponent(agentId)}/promote`, {
    from_env: fromEnv,
    to_env: toEnv,
    changelog,
  });
}
