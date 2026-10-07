export type ConductorSessionStatus =
  | 'ACTIVE'
  | 'COMPLETED'
  | 'EXPIRED'
  | 'ARCHIVED';

export type ConductorProposalStatus =
  | 'DRAFT'
  | 'GENERATING'
  | 'PROPOSED'
  | 'VALIDATING'
  | 'VALIDATED'
  | 'SIMULATING'
  | 'SIMULATED'
  | 'READY_FOR_REVIEW'
  | 'PARTIALLY_APPROVED'
  | 'APPROVED'
  | 'APPLYING'
  | 'APPLIED'
  | 'REJECTED'
  | 'FAILED'
  | 'STALE';

export type ConductorOperationType =
  | 'set'
  | 'replace'
  | 'add'
  | 'remove'
  | 'append'
  | 'delete'
  | 'move';

export type ConductorApprovalState = 'pending' | 'approved' | 'rejected';

export type ConductorRiskLevel = 'low' | 'medium' | 'high';

export interface ConductorEvidence {
  id: string;
  proposal_id: string;
  tenant_id: string;
  source_type: string;
  source_id: string;
  evidence_summary: string;
  evidence_payload: Record<string, unknown>;
  created_at: string;
}

export interface ConductorApprovalEntry {
  id: string;
  proposal_id: string;
  change_id: string | null;
  tenant_id: string;
  actor_user_id: string | null;
  action: string;
  reason: string;
  previous_state: string;
  new_state: string;
  created_at: string;
}

export interface ConductorChange {
  id: string;
  proposal_id: string;
  tenant_id: string;
  sequence: number;
  section: string;
  path: string;
  operation: ConductorOperationType;
  old_value: unknown;
  new_value: unknown;
  reason: string;
  evidence_ids: string[];
  risk_level: ConductorRiskLevel;
  validation_state: string;
  validation_messages: string[];
  simulation_state: string;
  approval_state: ConductorApprovalState;
  applied_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConductorFieldDiffItem {
  change_id: string;
  sequence: number;
  section: string;
  json_pointer: string;
  path: string;
  operation: ConductorOperationType;
  old_value: unknown;
  new_value: unknown;
  reason: string;
  risk_level: ConductorRiskLevel;
  approval_state: ConductorApprovalState;
  validation_state: string;
  simulation_state: string;
  is_text_diff: boolean;
  line_diff: string[];
}

export interface ConductorProposalDiff {
  proposal_id: string;
  agent_id: string;
  base_version_number: number;
  base_config_hash: string;
  candidate_config_hash: string;
  approved_candidate_config_hash: string;
  diff_hash: string;
  total_changes: number;
  approved_changes: number;
  rejected_changes: number;
  pending_changes: number;
  grouped_by_section: Record<string, ConductorFieldDiffItem[]>;
  changes: ConductorFieldDiffItem[];
  side_by_side: {
    base_config: Record<string, unknown>;
    candidate_config: Record<string, unknown>;
    approved_candidate_config: Record<string, unknown>;
  };
}

export interface ConductorValidationReport {
  valid?: boolean;
  status?: string;
  errors?: Array<{ field: string; message: string }>;
  warnings?: Array<{ field: string; message: string }>;
  candidate_hash?: string;
  validated_at?: string;
}

export interface ConductorSimulationSummary {
  test_run_id?: string;
  test_case_id?: string | null;
  status?: string;
  is_mock_provider?: boolean;
  scorecard_summary?: {
    status?: string;
    overall_score?: number | null;
    explanation?: string;
  };
  turn_count?: number;
  duration_ms?: number;
  simulated_at?: string;
}

export interface ConductorRiskSummary {
  overall_risk?: ConductorRiskLevel;
  total_changes?: number;
  by_risk_level?: Record<string, number>;
}

export interface ConductorProposal {
  id: string;
  session_id: string;
  tenant_id: string;
  environment_id: string | null;
  agent_id: string;
  agent_kind: 'voice' | 'chat';
  base_agent_version_id: string | null;
  base_version_number: number;
  base_config_hash: string;
  base_draft_etag: string;
  base_config_snapshot: Record<string, unknown>;
  request_text: string;
  summary: string;
  rationale: string;
  status: ConductorProposalStatus;
  validation_status: string;
  validation_report: ConductorValidationReport;
  simulation_status: string;
  simulation_summary: ConductorSimulationSummary;
  risk_summary: ConductorRiskSummary;
  candidate_config_snapshot: Record<string, unknown>;
  final_candidate_hash: string;
  resulting_agent_version_id: string | null;
  resulting_version_number: number | null;
  apply_idempotency_key: string | null;
  approved_change_hash: string | null;
  is_mock_provider: boolean;
  provider: string;
  model: string;
  correlation_id: string;
  error_code: string | null;
  error_message: string | null;
  production_published: boolean;
  ready_to_publish: boolean;
  changes: ConductorChange[];
  evidence: ConductorEvidence[];
  approvals: ConductorApprovalEntry[];
  created_by: string | null;
  created_at: string;
  updated_at: string;
  applied_at: string | null;
}

export interface ConductorSession {
  id: string;
  tenant_id: string;
  environment_id: string | null;
  agent_id: string;
  agent_kind: 'voice' | 'chat';
  starting_agent_version_id: string | null;
  starting_version_number: number;
  caller_user_id: string | null;
  origin_surface: string;
  status: ConductorSessionStatus;
  request_text: string;
  context_policy: Record<string, unknown>;
  context_snapshot: Record<string, unknown>;
  correlation_id: string;
  proposals: ConductorProposal[];
  created_at: string;
  updated_at: string;
  expires_at: string | null;
}

export interface ConductorSessionCreatePayload {
  agent_id: string;
  agent_kind?: 'voice' | 'chat';
  base_version_number?: number;
  origin_surface?: string;
  request_text?: string;
  context_policy?: {
    include_calls?: boolean;
    include_qa_scorecards?: boolean;
    include_test_runs?: boolean;
    include_tools?: boolean;
    include_knowledge_bases?: boolean;
    include_workflows?: boolean;
    call_ids?: string[];
    test_run_ids?: string[];
    suite_ids?: string[];
  };
}

export interface ConductorPromptPayload {
  session_id?: string;
  agent_id?: string;
  agent_kind?: 'voice' | 'chat';
  base_version_number?: number;
  request_text: string;
  origin_surface?: string;
  call_ids?: string[];
  test_run_ids?: string[];
  explicit_operations?: Array<{
    path: string;
    operation: ConductorOperationType;
    old_value?: unknown;
    new_value?: unknown;
    from_path?: string;
    reason?: string;
    risk_level?: ConductorRiskLevel;
    evidence_ids?: string[];
  }>;
  auto_validate?: boolean;
  auto_simulate?: boolean;
  idempotency_key?: string;
}

export interface ConductorSimulatePayload {
  input_messages?: string[];
  dynamic_variables?: Record<string, unknown>;
  evaluation_rules?: Array<Record<string, unknown>>;
  persist_reproduction_test_case?: boolean;
  test_case_name?: string;
  suite_id?: string;
  allow_mock_fallback?: boolean;
}

export interface ConductorApplyPayload {
  idempotency_key?: string;
  expected_base_version_number?: number;
  expected_base_config_hash?: string;
  version_notes?: string;
}

export interface ConductorApplyResult {
  proposal: ConductorProposal;
  resulting_version: {
    id: string;
    agent_id: string;
    version_number: number;
    config_hash: string;
    config_snapshot: Record<string, unknown>;
    notes?: string;
    published_by?: string;
    published_at?: string;
    production_published: boolean;
    ready_to_publish: boolean;
  };
  applied_change_ids: string[];
  skipped_change_ids: string[];
  base_version_number: number;
  resulting_version_number: number;
  base_config_hash: string;
  resulting_config_hash: string;
  production_published: boolean;
  ready_to_publish: boolean;
  idempotent_replay: boolean;
}
