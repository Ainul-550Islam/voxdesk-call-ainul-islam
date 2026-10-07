/**
 * dashboard/src/api/agent-voices.ts
 *
 * TTS voice catalog client, served by `GET /api/agents/voices`
 * (`app/api/agent_catalog_routes.py`).
 *
 * The previous version of this file called `/api/agents/{id}/agent-voices`
 * and `/api/agent-voices`, neither of which exists, and returned `null` / `[]`
 * on failure — which is why the Agent Studio voice picker rendered empty.
 *
 * Truthfulness contract (mirrors the backend):
 * - `configured` means an API key exists in deployment settings. It does NOT
 *   mean the provider was reached.
 * - `reachable` / `authenticated` are always the string "not_checked".
 * - `voices[]` lists only the voice IDs this deployment has configured. A
 *   provider's full voice library is never invented; `voice_library_fetched`
 *   is always false here.
 */

import { apiClient } from './client';

export interface VoiceProviderState {
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

export interface CatalogVoice {
  voice_id: string;
  provider: string;
  source: string;
  label: string;
}

export interface VoicesCatalog {
  providers: VoiceProviderState[];
  voices: CatalogVoice[];
  default_provider: string;
  default_voice_id: string;
  voice_library_fetched: boolean;
  voice_library_note: string;
  generated_at: string;
  notes: string[];
}

export async function getVoiceProviders(): Promise<VoicesCatalog> {
  return apiClient.get<VoicesCatalog>('/api/agents/voices');
}

/** Backwards-compatible alias used by `dashboard/src/api/agents.ts`. */
export const listAgentVoices = getVoiceProviders;

export async function getAgentVoices(_agentId: string): Promise<VoicesCatalog> {
  // Voice availability is a property of the deployment, not of one agent, so
  // the per-agent argument is accepted for call-site compatibility but is not
  // part of the request.
  return getVoiceProviders();
}
