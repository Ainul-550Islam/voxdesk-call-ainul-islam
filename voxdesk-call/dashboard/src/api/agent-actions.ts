/** dashboard/src/api/agent-actions.ts — Durable Agent Lifecycle & Action API client */
import { apiClient } from './client';
import type { DurableAgentRecord } from './types/agent';
import type {
  AgentVersionSnapshot,
  PublishAgentVersionInput,
  RollbackAgentVersionInput,
} from './types/agent-version';

export async function getAgentActions(agentId: string): Promise<any> {
  return apiClient.get(`/api/agents/${encodeURIComponent(agentId)}`);
}

export async function listAgentActions(): Promise<any[]> {
  const res = await apiClient.get<any>('/api/agents');
  return Array.isArray(res) ? res : res?.items || res?.agents || [];
}

export async function publishAgent(
  agentId: string,
  input: PublishAgentVersionInput = {}
): Promise<AgentVersionSnapshot> {
  return apiClient.post<AgentVersionSnapshot>(
    `/api/agents/${encodeURIComponent(agentId)}/publish`,
    {
      changelog: input.changelog ?? input.release_notes ?? '',
      environment: input.environment ?? 'production',
    }
  );
}

export async function rollbackAgentVersion(
  agentId: string,
  input: RollbackAgentVersionInput
): Promise<AgentVersionSnapshot> {
  const targetVersion = input.target_version ?? input.version ?? 1;
  return apiClient.post<AgentVersionSnapshot>(
    `/api/agents/${encodeURIComponent(agentId)}/rollback`,
    {
      target_version: targetVersion,
      reason: input.reason ?? '',
    }
  );
}

export async function archiveAgent(agentId: string, reason = ''): Promise<any> {
  return apiClient.post(`/api/v1/agents/${encodeURIComponent(agentId)}/archive`, {
    reason,
  });
}

export async function restoreAgent(agentId: string): Promise<any> {
  return apiClient.post(`/api/v1/agents/${encodeURIComponent(agentId)}/restore`, {});
}

export async function deleteAgent(agentId: string): Promise<any> {
  return apiClient.delete(`/api/v1/agents/${encodeURIComponent(agentId)}`);
}

export async function duplicateAgent(
  sourceAgent: DurableAgentRecord,
  options?: { name?: string }
): Promise<any> {
  const newName = (options?.name ?? `${sourceAgent.name} (Copy)`).trim().slice(0, 80);
  return apiClient.post(`/api/v1/agents/${encodeURIComponent(sourceAgent.id)}/clone`, {
    new_name: newName,
    include_knowledge_bases: true,
    include_tools: true,
  });
}
