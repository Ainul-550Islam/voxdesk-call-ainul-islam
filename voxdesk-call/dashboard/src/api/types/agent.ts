import type {
  Agent,
  AgentStatus,
  AgentType,
  AgentLanguage,
  AgentModelProvider,
  AgentVoice,
  AgentModel,
  AgentKnowledgeBase,
  AgentTool,
  AgentConfig,
  AgentCreateRequest,
  AgentUpdateRequest,
  AgentTemplate,
  AgentListResponse,
  AgentMetrics,
} from '../../types/agent';

export type {
  Agent,
  AgentStatus,
  AgentType,
  AgentLanguage,
  AgentModelProvider,
  AgentVoice,
  AgentModel,
  AgentKnowledgeBase,
  AgentTool,
  AgentConfig,
  AgentCreateRequest,
  AgentUpdateRequest,
  AgentTemplate,
  AgentListResponse,
  AgentMetrics,
};

export interface DurableAgentRecord extends Agent {
  external_key?: string;
  agent_type?: 'voice' | 'chat' | 'multimodal' | string;
  environment_id?: string | null;
  active_version?: number;
  active_version_number?: number | null;
  published_version_id?: string | null;
  published_version_number?: number | null;
  draft_etag?: string;
  lock_version?: number;
  validation_status?: 'valid' | 'invalid' | 'unvalidated' | string;
  validation_errors?: Array<{
    field: string;
    code: string;
    message: string;
  }>;
  greeting?: string;
  persona?: string;
  primary_language?: string;
  fallback_languages?: string[];
  voice_id?: string;
  speech_speed?: number;
  llm_provider?: string;
  llm_model?: string;
  llm_temperature?: number;
  handoff_mode?: string;
  handoff_destination?: string;
  record_calls?: boolean;
  pii_redaction?: boolean;
  enabled_tools?: string[];
  knowledge_source_ids?: string[];
  operating_timezone?: string;
  operating_window?: string;
  archived_at?: string | null;
}

export interface AgentValidationIssue {
  field: string;
  code: string;
  message: string;
  severity?: 'error' | 'warning';
}

export interface AgentValidationResult {
  valid: boolean;
  errors: AgentValidationIssue[];
  warnings: AgentValidationIssue[];
  checked_at: string;
}

export interface AgentTestResult {
  ok: boolean;
  agent_id: string;
  config_hash: string;
  problems: string[];
  checks: Record<string, boolean | number>;
}
