/**
 * dashboard/src/api/agent-templates.ts
 *
 * Agent "templates" — reusable starting configurations.
 *
 * There is no `GET /api/agents/templates` endpoint and no agent-template table
 * in this system, and the previous version of this file invented one by calling
 * `/api/agents/{id}/agent-templates` (404) and returning `[]`. Rather than
 * fabricate a catalog, templates are derived from something that genuinely
 * exists: **this tenant's own agents**, fetched from
 * `GET /api/v1/agents` (`app/api/agent_builder_routes.py`).
 *
 * A template is therefore an honest object: "a published or draft agent in this
 * tenant that can be used as the starting point for a new one". Copying one
 * goes through the real clone endpoint
 * (`POST /api/v1/agents/{agent_id}/clone`), so nothing is simulated.
 */

import { apiClient } from './client';
import { listAgents } from './agents';

export interface AgentTemplate {
  id: string;
  name: string;
  description: string;
  /** Where this starting point came from. Always `existing_agent`. */
  source: 'existing_agent';
  /** The agent's own status, so the UI can mark drafts as unfinished. */
  status: string;
  agent_type: string;
  language: string;
  created_at: string;
  updated_at: string;
}

export interface AgentTemplateList {
  templates: AgentTemplate[];
  total: number;
  /**
   * Always false. This system has no shipped template library; every entry is
   * one of the tenant's own agents. Kept as an explicit field so the UI can
   * say so instead of implying a vendor catalog exists.
   */
  has_library_templates: boolean;
}

function toTemplate(raw: Record<string, unknown>): AgentTemplate {
  return {
    id: String(raw.id ?? raw.agent_id ?? ''),
    name: String(raw.name ?? 'Untitled agent'),
    description: String(raw.description ?? ''),
    source: 'existing_agent',
    status: String(raw.status ?? 'DRAFT'),
    agent_type: String(raw.type ?? raw.agent_type ?? 'VOICE'),
    language: String(raw.language ?? 'en-US'),
    created_at: String(raw.created_at ?? ''),
    updated_at: String(raw.updated_at ?? ''),
  };
}

export async function listAgentTemplates(): Promise<AgentTemplateList> {
  const res = await listAgents();
  const templates = res.agents
    .filter((agent) => Boolean(agent.id))
    .map((agent) => toTemplate(agent as unknown as Record<string, unknown>));
  return {
    templates,
    total: templates.length,
    has_library_templates: false,
  };
}

/**
 * Create a new agent from a template (i.e. clone one of the tenant's agents).
 * Uses the real lifecycle endpoint; the returned record is the new agent.
 */
export async function createAgentFromTemplate(
  templateId: string,
  newName: string,
): Promise<{ id: string; name: string }> {
  const res = await apiClient.post<Record<string, unknown>>(
    `/api/v1/agents/${encodeURIComponent(templateId)}/clone`,
    {
      new_name: newName,
      include_knowledge_bases: true,
      include_tools: true,
    },
  );
  return {
    id: String(res?.new_agent_id ?? res?.id ?? ''),
    name: String(res?.name ?? newName),
  };
}

export async function getAgentTemplates(): Promise<AgentTemplateList> {
  return listAgentTemplates();
}
