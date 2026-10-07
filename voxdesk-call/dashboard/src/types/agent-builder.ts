/** dashboard/src/types/agent-builder.ts — Builder domain types */
export type BuilderSection =
  | 'overview'
  | 'prompt'
  | 'voice'
  | 'model'
  | 'conversation'
  | 'knowledge'
  | 'tools'
  | 'call_handling'
  | 'security'
  | 'versions'
  | 'test'
  | 'publish';

export type SaveState = 'SAVED' | 'SAVING' | 'UNSAVED' | 'ERROR' | 'CONFLICT';

export interface ValidationError {
  field: string;
  message: string;
  severity: 'error' | 'warning';
  code?: string;
}

export interface ValidationResult {
  valid: boolean;
  errors: ValidationError[];
  warnings: ValidationError[];
  validated_at?: string;
}

export interface BuilderConfig {
  id: string;
  tenant_id: string;
  name: string;
  description?: string;
  status: string;
  type: string;
  language?: string;
  voice_id?: string;
  model_id?: string;
  system_prompt?: string;
  config?: Record<string, unknown>;
  version?: number;
  etag?: string;
  updated_at?: string;
}

export interface PublishResult {
  success: boolean;
  agent_id: string;
  version?: number;
  published_at?: string;
  errors?: ValidationError[];
}

export interface Version {
  id: string;
  version: number;
  created_at: string;
  author?: string;
  status: string;
  changes?: string;
  is_current?: boolean;
  etag?: string;
  config_hash?: string;
  config_snapshot?: Record<string, any>;
  published_environment?: string;
  is_rollback?: boolean;
}

export interface BuilderState {
  config: BuilderConfig | null;
  localConfig: BuilderConfig | null;
  isDirty: boolean;
  saveState: SaveState;
  lastSaved?: string;
  error?: string;
  conflict?: boolean;
}

export const BUILDER_SECTIONS: {
  id: BuilderSection;
  label: string;
  description: string;
}[] = [
  { id: 'overview', label: 'Overview', description: 'Identity & status overview' },
  { id: 'prompt', label: 'Prompt', description: 'System instructions & persona' },
  { id: 'voice', label: 'Voice', description: 'Voice synthesis & prosody' },
  { id: 'model', label: 'Model', description: 'LLM provider & temperature' },
  { id: 'conversation', label: 'Conversation', description: 'Turn-taking & greeting' },
  { id: 'knowledge', label: 'Knowledge', description: 'RAG knowledge bases' },
  { id: 'tools', label: 'Tools', description: 'Function calling & webhooks' },
  { id: 'call_handling', label: 'Call Handling', description: 'Transfers, DTMF & silence' },
  { id: 'security', label: 'Security', description: 'PII redaction & retention' },
  { id: 'versions', label: 'Versions', description: 'Immutable version history' },
  { id: 'test', label: 'Test', description: 'Readiness & simulation' },
  { id: 'publish', label: 'Publish', description: 'Validate & publish release' },
];
