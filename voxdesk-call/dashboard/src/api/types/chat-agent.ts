export type ChatAgentLifecycleStatus = 'draft' | 'published' | 'archived';

export interface ChatAgentConfig {
  system_prompt?: string;
  first_message?: string;
  greeting?: string;
  model?: string;
  provider?: string;
  temperature?: number;
  max_tokens?: number;
  knowledge_base_ids?: string[];
  tools?: string[];
  [key: string]: unknown;
}

export interface ChatAgentRecord {
  id: string;
  tenant_id: string;
  name: string;
  description: string;
  status: ChatAgentLifecycleStatus;
  draft_config: ChatAgentConfig;
  published_config?: ChatAgentConfig | null;
  draft_version: number;
  published_version?: number | null;
  etag?: string;
  created_by?: string | null;
  archived_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChatAgentVersionRecord {
  id: string;
  tenant_id: string;
  chat_agent_id: string;
  version: number;
  status: 'draft' | 'published' | 'retired' | string;
  config: ChatAgentConfig;
  config_hash?: string;
  change_summary: string;
  created_by?: string | null;
  created_at: string;
  published_at?: string | null;
}

export interface ChatAgentValidationResponse {
  valid: boolean;
  errors: string[];
  warnings: string[];
  etag: string;
}

export interface ChatSessionRecord {
  id: string;
  tenant_id: string;
  environment_id?: string | null;
  chat_agent_id: string;
  chat_agent_version?: number | null;
  contact_id?: string | null;
  channel: 'web' | 'sms' | 'whatsapp' | 'api' | string;
  status: 'active' | 'completed' | 'escalated' | 'expired' | string;
  dynamic_variables: Record<string, unknown>;
  metadata: Record<string, unknown>;
  message_count: number;
  started_at: string;
  last_message_at?: string | null;
  ended_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChatMessageRecord {
  id: string;
  tenant_id: string;
  session_id: string;
  sequence: number;
  role: 'user' | 'assistant' | 'system' | 'tool' | string;
  content: string;
  tool_calls: Record<string, unknown>[];
  metadata: Record<string, unknown>;
  latency_ms?: number | null;
  created_at: string;
}

export interface ChatTurnResult {
  session: ChatSessionRecord;
  user_message: ChatMessageRecord;
  assistant_message: ChatMessageRecord;
  memory_keys_used: string[];
  memory_keys_saved: string[];
}
