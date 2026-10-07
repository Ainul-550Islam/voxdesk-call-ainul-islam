export type AgentVersionLifecycleStatus =
  | 'published'
  | 'superseded'
  | 'rolled_back'
  | 'draft'
  | 'retired';

export type AgentPublishEnvironment = 'development' | 'staging' | 'production';

export interface AgentVersionSnapshot {
  id?: string;
  agent_id: string;
  version: number;
  version_number?: number;
  version_label?: string;
  status: AgentVersionLifecycleStatus | string;
  config_hash: string;
  published_at: string;
  published_by: string;
  changelog: string;
  release_notes?: string;
  published_environment?: AgentPublishEnvironment | string;
  published_environment_id?: string | null;
  source_version_id?: string | null;
  is_rollback?: boolean;
  is_active?: boolean;
  config_snapshot?: Record<string, unknown>;
}

export interface AgentVersionFieldDiff {
  field_path: string;
  change_type: 'added' | 'removed' | 'modified' | 'unchanged';
  old_value?: unknown;
  new_value?: unknown;
}

export interface AgentVersionDiffResponse {
  agent_id: string;
  from_version: number;
  to_version: number;
  total_changes: number;
  diffs: AgentVersionFieldDiff[];
}

export interface PublishAgentVersionInput {
  changelog?: string;
  release_notes?: string;
  environment?: AgentPublishEnvironment;
  environment_id?: string;
}

export interface RollbackAgentVersionInput {
  target_version?: number;
  version?: number;
  reason?: string;
}
