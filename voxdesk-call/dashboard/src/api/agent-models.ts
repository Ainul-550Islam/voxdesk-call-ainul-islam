/**
 * dashboard/src/api/agent-models.ts
 *
 * LLM model catalog client, served by `GET /api/agents/models`
 * (`app/api/agent_catalog_routes.py`).
 *
 * The previous version of this file called `/api/agents/{id}/agent-models`
 * and `/api/agent-models`, neither of which exists, and returned `null` / `[]`
 * on failure — which is why the Agent Studio model picker rendered empty.
 *
 * Truthfulness contract (mirrors the backend):
 * - `presets[].model` comes from `app.agent.llm_factory.PRESETS`: the exact
 *   provider/model pairs the runtime constructs. No model name is guessed.
 * - `configured` means an API key exists in deployment settings. It does NOT
 *   mean the provider was reached.
 * - `reachable` / `authenticated` are always the string "not_checked".
 * - `default_provider` / `default_preset` are derived from the first
 *   selectable preset, and are `null` when nothing is selectable. Callers must
 *   render "not configured" rather than inventing a selection.
 */

import { apiClient } from './client';

export interface ModelProviderState {
  id: string;
  label: string;
  allowed_by_domain: boolean;
  configured: boolean;
  installed: boolean;
  contract_declared: boolean;
  buildable: boolean;
  selectable: boolean;
  distribution: string | null;
  sdk_version: string | null;
  capabilities: string[];
  runtime_probe: string;
  reachable: string;
  authenticated: string;
  reason: string | null;
}

export interface ModelPreset {
  preset: string;
  provider: string;
  model: string;
  est_latency_ms: number;
  notes: string;
  configured: boolean;
  installed: boolean;
  selectable: boolean;
}

export interface ModelsCatalog {
  providers: ModelProviderState[];
  presets: ModelPreset[];
  default_provider: string | null;
  default_provider_source: string;
  default_preset: string | null;
  configured_stt_model: string;
  configured_tts_model: string;
  generated_at: string;
  notes: string[];
}

export async function getModelProviders(): Promise<ModelsCatalog> {
  return apiClient.get<ModelsCatalog>('/api/agents/models');
}

/** Backwards-compatible alias. */
export const listAgentModels = getModelProviders;

export async function getAgentModels(_agentId: string): Promise<ModelsCatalog> {
  // Model availability is a property of the deployment, not of one agent, so
  // the per-agent argument is accepted for call-site compatibility but is not
  // part of the request.
  return getModelProviders();
}
