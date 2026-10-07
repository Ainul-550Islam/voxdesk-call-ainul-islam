/**
 * dashboard/src/types/agent.ts
 * Core Agent domain types mapped to durable backend API responses.
 */
export type AgentStatus =
  | 'DRAFT'
  | 'VALIDATING'
  | 'VALID'
  | 'INVALID'
  | 'PUBLISHED'
  | 'UNPUBLISHED'
  | 'ARCHIVED'
  | 'RETIRED'
  | 'ERROR';

export type AgentType =
  | 'INBOUND'
  | 'OUTBOUND'
  | 'HYBRID'
  | 'CHAT'
  | 'VOICE'
  | 'OMNI';

export type AgentLanguage =
  | 'en'
  | 'en-US'
  | 'en-GB'
  | 'es'
  | 'fr'
  | 'de'
  | 'bn'
  | 'hi'
  | string;

export type AgentModelProvider =
  | 'openai'
  | 'anthropic'
  | 'google'
  | 'cohere'
  | 'custom';

export interface AgentVoice {
  id: string;
  name: string;
  provider: string;
  language: string;
  gender?: 'masculine' | 'feminine' | 'neutral';
  preview_url?: string;
  is_custom?: boolean;
}

export interface AgentModel {
  id: string;
  name: string;
  provider: string;
  context_window?: number;
  streaming?: boolean;
}

export interface AgentKnowledgeBase {
  id: string;
  name: string;
  description?: string;
  document_count?: number;
  attached_at?: string;
}

export interface AgentTool {
  id: string;
  name: string;
  description?: string;
  enabled: boolean;
  config?: Record<string, unknown>;
}

export interface AgentConfig {
  system_prompt?: string;
  voice_id?: string;
  model_id?: string;
  language?: string;
  welcome_message?: string;
  transfer_number?: string;
  voicemail_behavior?: string;
  max_call_duration?: number;
  interruption_enabled?: boolean;
  [key: string]: unknown;
}

export interface Agent {
  id: string;
  external_key?: string;
  tenant_id: string;
  environment_id?: string | null;
  name: string;
  description?: string;
  status: AgentStatus;
  type: AgentType;
  voice?: AgentVoice;
  language?: string;
  primary_language?: string;
  model?: AgentModel;
  llm_provider?: string;
  llm_model?: string;
  voice_id?: string;
  speech_speed?: number;
  greeting?: string;
  persona?: string;
  system_prompt?: string;
  handoff_mode?: string;
  handoff_destination?: string;
  operating_window?: string;
  operating_timezone?: string;
  config?: AgentConfig;
  calls_count?: number;
  created_at: string;
  updated_at: string;
  published_at?: string;
  version?: number;
  active_version?: number | null;
  published_version_number?: number | null;
  etag?: string;
  draft_etag?: string;
  validation_status?: 'valid' | 'invalid' | 'unvalidated' | string;
}

export interface AgentCreateRequest {
  name: string;
  description?: string;
  type: AgentType;
  language?: string;
  voice_id?: string;
  model_id?: string;
  system_prompt?: string;
  config?: AgentConfig;
  template_id?: string;
}

export interface AgentUpdateRequest {
  name?: string;
  description?: string;
  type?: AgentType;
  language?: string;
  voice_id?: string;
  model_id?: string;
  system_prompt?: string;
  config?: AgentConfig;
  expected_etag?: string;
}

export interface AgentTemplate {
  id: string;
  name: string;
  description?: string;
  type: AgentType;
  language?: string;
  system_prompt?: string;
  config?: AgentConfig;
  is_empty?: boolean;
  preview?: string;
}

export interface AgentListResponse {
  agents: Agent[];
  total: number;
  page?: number;
  page_size?: number;
}

export interface AgentMetrics {
  total: number;
  published: number;
  draft: number;
  archived: number;
  total_calls: number;
}
