/**
 * dashboard/src/api/agent-tools.ts
 *
 * Agent tool-registry API client.
 *
 * Every path below is served by `app/api/tool_registry_routes.py`:
 *
 *   POST   /api/agents/{agent_id}/tools
 *   GET    /api/agents/{agent_id}/tools
 *   GET    /api/agents/{agent_id}/tools/{tool_id}
 *   PATCH  /api/agents/{agent_id}/tools/{tool_id}
 *   DELETE /api/agents/{agent_id}/tools/{tool_id}
 *   POST   /api/agents/{agent_id}/tools/{tool_id}/enable
 *   POST   /api/agents/{agent_id}/tools/{tool_id}/disable
 *
 * The previous version of this file called `/api/agents/{id}/agent-tools`
 * and `/api/agent-tools`, neither of which exists, and swallowed the 404 into
 * `null` / `[]`. These wrappers propagate real failures instead.
 *
 * Note the distinction kept throughout: these are the *registered custom
 * functions* for an agent. The built-in tool contracts the runtime dispatches
 * (`book_appointment`, `transfer_to_human`, ...) live in
 * `app/agent/functions.py` and are exposed by `GET /api/agents/tools/catalog`
 * (see `agentCatalog.ts`), not here.
 */

import { apiClient } from './client';

export interface AgentToolParameter {
  name: string;
  type?: 'string' | 'number' | 'boolean' | 'object' | 'array';
  description?: string;
  required?: boolean;
}

export interface AgentTool {
  id: string;
  tenant_id: string;
  agent_id: string;
  name: string;
  description: string;
  schema: Record<string, unknown>;
  auth_binding: Record<string, unknown>;
  is_enabled: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface CreateAgentToolInput {
  name: string;
  description?: string;
  schema?: Record<string, unknown>;
  auth_binding?: Record<string, unknown>;
  is_enabled?: boolean;
}

export interface UpdateAgentToolInput {
  description?: string;
  schema?: Record<string, unknown>;
  auth_binding?: Record<string, unknown>;
  is_enabled?: boolean;
}

function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

function encode(value: string): string {
  return encodeURIComponent(value);
}

export async function listAgentTools(agentId: string): Promise<AgentTool[]> {
  const res = await apiClient.get<unknown>(`/api/agents/${encode(agentId)}/tools`);
  if (Array.isArray(res)) return res as AgentTool[];
  const items = (res as { items?: unknown; tools?: unknown } | null) ?? {};
  return asArray<AgentTool>(items.items ?? items.tools);
}

export async function getAgentTool(agentId: string, toolId: string): Promise<AgentTool> {
  return apiClient.get<AgentTool>(`/api/agents/${encode(agentId)}/tools/${encode(toolId)}`);
}

export async function createAgentTool(
  agentId: string,
  input: CreateAgentToolInput,
): Promise<AgentTool> {
  return apiClient.post<AgentTool>(`/api/agents/${encode(agentId)}/tools`, {
    name: input.name,
    description: input.description ?? '',
    schema: input.schema ?? {},
    auth_binding: input.auth_binding ?? {},
    is_enabled: input.is_enabled ?? true,
  });
}

export async function updateAgentTool(
  agentId: string,
  toolId: string,
  input: UpdateAgentToolInput,
): Promise<AgentTool> {
  const body: Record<string, unknown> = {};
  if (input.description !== undefined) body.description = input.description;
  if (input.schema !== undefined) body.schema = input.schema;
  if (input.auth_binding !== undefined) body.auth_binding = input.auth_binding;
  if (input.is_enabled !== undefined) body.is_enabled = input.is_enabled;
  return apiClient.patch<AgentTool>(
    `/api/agents/${encode(agentId)}/tools/${encode(toolId)}`,
    body,
  );
}

export async function deleteAgentTool(agentId: string, toolId: string): Promise<void> {
  await apiClient.delete(`/api/agents/${encode(agentId)}/tools/${encode(toolId)}`);
}

export async function enableAgentTool(agentId: string, toolId: string): Promise<AgentTool> {
  return apiClient.post<AgentTool>(
    `/api/agents/${encode(agentId)}/tools/${encode(toolId)}/enable`,
    {},
  );
}

export async function disableAgentTool(agentId: string, toolId: string): Promise<AgentTool> {
  return apiClient.post<AgentTool>(
    `/api/agents/${encode(agentId)}/tools/${encode(toolId)}/disable`,
    {},
  );
}
