/**
 * TypeScript domain models for Real Testing, Simulation, Batch Evaluation,
 * Web/Phone Call Testing, and Evidence-Backed QA Scorecards (Prompt 3).
 */

export type TestSuiteStatus = 'active' | 'archived';
export type TestPassPolicy = 'all_passed' | 'min_score';
export type AgentKind = 'voice' | 'chat';

export type TestRunMode =
  | 'llm'
  | 'simulation'
  | 'web_call'
  | 'phone_call'
  | 'chat';

export type TestRunStatus =
  | 'queued'
  | 'running'
  | 'passed'
  | 'failed'
  | 'error'
  | 'cancelled'
  | 'not_run';

export type EvaluationRuleType =
  | 'contains'
  | 'not_contains'
  | 'regex'
  | 'json_path_equals'
  | 'json_path_exists'
  | 'tool_called'
  | 'tool_not_called'
  | 'transfer_occurred'
  | 'variable_equals'
  | 'turn_count_max'
  | 'turn_count_min'
  | 'latency_ms_max'
  | 'final_state_equals'
  | 'llm_judge';

export type EvaluationResultStatus =
  | 'passed'
  | 'failed_assertion'
  | 'evaluation_error'
  | 'skipped'
  | 'no_assertions';

export type ScorecardStatus =
  | 'PASSED'
  | 'FAILED_ASSERTION'
  | 'EVALUATION_ERROR'
  | 'NO_ASSERTIONS'
  | 'NOT_RUN';

export interface InlineEvaluationRuleSpec {
  name: string;
  rule_type: EvaluationRuleType;
  config: Record<string, unknown>;
  enabled?: boolean;
  weight?: number;
  evaluator_version?: string;
}

export interface SimulationTranscriptTurn {
  role: 'user' | 'assistant' | 'system' | 'tool' | string;
  content: string;
  turn_index?: number;
  intent?: string;
  tool_calls?: Array<{
    id?: string;
    name?: string;
    tool_name?: string;
    arguments?: Record<string, unknown>;
    result?: unknown;
    turn_index?: number;
  }>;
  latency_ms?: number | null;
  timestamp?: string;
}

export interface SimulationEventEntry {
  event: string;
  timestamp?: string;
  turn_index?: number;
  tool_name?: string;
  destination?: string;
  error_code?: string;
  error_message?: string;
  [key: string]: unknown;
}

export interface EvaluationEvidence {
  matched_turn_indices?: number[];
  matched_snippets?: string[];
  expected?: unknown;
  actual?: unknown;
  tool_calls_inspected?: Array<Record<string, unknown>>;
  judge_model?: string | null;
  judge_provider?: string | null;
  judge_prompt_version?: string | null;
  judge_rationale?: string | null;
  is_mock_judge?: boolean;
  error_detail?: string | null;
  [key: string]: unknown;
}

export interface EvaluationRule {
  id: string;
  tenant_id: string;
  suite_id?: string | null;
  test_case_id?: string | null;
  name: string;
  rule_type: EvaluationRuleType;
  config: Record<string, unknown>;
  evaluator_version: string;
  enabled: boolean;
  weight: number;
  created_by?: string | null;
  created_at: string;
  updated_at: string;
}

export interface EvaluationRuleCreatePayload {
  suite_id?: string | null;
  test_case_id?: string | null;
  name: string;
  rule_type: EvaluationRuleType;
  config: Record<string, unknown>;
  evaluator_version?: string;
  enabled?: boolean;
  weight?: number;
}

export interface EvaluationRuleUpdatePayload {
  name?: string;
  rule_type?: EvaluationRuleType;
  config?: Record<string, unknown>;
  evaluator_version?: string;
  enabled?: boolean;
  weight?: number;
}

export interface EvaluationResult {
  id: string;
  tenant_id: string;
  test_run_id: string;
  evaluation_rule_id?: string | null;
  rule_name: string;
  rule_type: EvaluationRuleType | string;
  status: EvaluationResultStatus;
  score: number;
  weight: number;
  evidence: EvaluationEvidence;
  explanation: string;
  evaluator_version: string;
  formula_version: string;
  created_at: string;
}

export interface QAScorecardSummary {
  status: ScorecardStatus;
  overall_score: number | null;
  pass_threshold?: number;
  formula_version?: string;
  evaluator_version?: string;
  total_rules?: number;
  enabled_rules?: number;
  passed_count?: number;
  failed_assertion_count?: number;
  evaluation_error_count?: number;
  skipped_count?: number;
  weighted_earned?: number;
  weighted_possible?: number;
  explanation: string;
  evaluated_at?: string | null;
}

export interface TestSuite {
  id: string;
  tenant_id: string;
  environment_id?: string | null;
  name: string;
  description: string;
  status: TestSuiteStatus;
  pass_policy: TestPassPolicy;
  min_pass_score: number;
  case_count: number;
  rule_count: number;
  last_run_at?: string | null;
  last_run_status?: TestRunStatus | null;
  created_by?: string | null;
  archived_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface TestSuiteCreatePayload {
  name: string;
  description?: string;
  environment_id?: string | null;
  pass_policy?: TestPassPolicy;
  min_pass_score?: number;
}

export interface TestSuiteUpdatePayload {
  name?: string;
  description?: string;
  status?: TestSuiteStatus;
  pass_policy?: TestPassPolicy;
  min_pass_score?: number;
}

export interface TestCase {
  id: string;
  tenant_id: string;
  suite_id?: string | null;
  agent_id: string;
  agent_kind: AgentKind;
  agent_version_id?: string | null;
  agent_version_number: number;
  name: string;
  mode: TestRunMode;
  input_messages: Array<Record<string, unknown>>;
  dynamic_variables: Record<string, unknown>;
  metadata: Record<string, unknown>;
  expected_rules: InlineEvaluationRuleSpec[];
  enabled: boolean;
  archived_at?: string | null;
  created_by?: string | null;
  created_at: string;
  updated_at: string;
}

export interface TestCaseCreatePayload {
  suite_id?: string | null;
  agent_id: string;
  agent_kind?: AgentKind;
  agent_version_number: number;
  name: string;
  mode?: TestRunMode;
  input_messages: Array<Record<string, unknown> | string>;
  dynamic_variables?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  expected_rules?: InlineEvaluationRuleSpec[];
  enabled?: boolean;
}

export interface TestCaseUpdatePayload {
  name?: string;
  agent_id?: string;
  agent_kind?: AgentKind;
  agent_version_number?: number;
  mode?: TestRunMode;
  input_messages?: Array<Record<string, unknown> | string>;
  dynamic_variables?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  expected_rules?: InlineEvaluationRuleSpec[];
  enabled?: boolean;
}

export interface TestRun {
  id: string;
  tenant_id: string;
  environment_id?: string | null;
  suite_id?: string | null;
  batch_id?: string | null;
  test_case_id?: string | null;
  agent_id: string;
  agent_kind: AgentKind;
  agent_version_id?: string | null;
  agent_version_number: number;
  agent_config_hash: string;
  pinned_config_snapshot: Record<string, unknown>;
  mode: TestRunMode;
  status: TestRunStatus;
  is_mock_provider: boolean;
  provider: string;
  model: string;
  correlation_id: string;
  call_id?: string | null;
  chat_session_id?: string | null;
  transcript_snapshot: SimulationTranscriptTurn[];
  events_snapshot: SimulationEventEntry[];
  usage_metadata: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
    turn_count?: number;
    [key: string]: unknown;
  };
  latency_metadata: {
    total_duration_ms?: number;
    avg_turn_latency_ms?: number;
    max_turn_latency_ms?: number;
    p95_ms?: number;
    [key: string]: unknown;
  };
  final_output: {
    final_state?: string;
    transferred?: boolean;
    transfer_destination?: string | null;
    variables?: Record<string, unknown>;
    dynamic_variables?: Record<string, unknown>;
    webrtc_state?: string;
    last_reply?: string;
    [key: string]: unknown;
  };
  scorecard_summary: QAScorecardSummary;
  error_code?: string | null;
  error_message?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  duration_ms?: number | null;
  created_by?: string | null;
  created_at: string;
  updated_at: string;
  evaluation_results: EvaluationResult[];
}

export interface LLMPlaygroundRunPayload {
  agent_id: string;
  agent_kind?: AgentKind;
  agent_version_number: number;
  prompt_override?: string | null;
  user_message: string;
  conversation_history?: Array<Record<string, unknown>>;
  dynamic_variables?: Record<string, unknown>;
  evaluation_rules?: InlineEvaluationRuleSpec[];
  allow_mock_fallback?: boolean;
}

export interface MultiTurnSimulationPayload {
  agent_id: string;
  agent_kind?: AgentKind;
  agent_version_number: number;
  suite_id?: string | null;
  test_case_id?: string | null;
  mode?: TestRunMode;
  input_messages: Array<Record<string, unknown> | string>;
  dynamic_variables?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  evaluation_rules?: InlineEvaluationRuleSpec[];
  allow_mock_fallback?: boolean;
}

export interface BatchSuiteRunPayload {
  case_ids?: string[];
  agent_version_override?: number;
  dynamic_variables_override?: Record<string, unknown>;
  allow_mock_fallback?: boolean;
}

export interface BatchRunAggregationSummary {
  batch_id: string;
  suite_id: string;
  suite_name: string;
  overall_status: TestRunStatus;
  total_cases: number;
  queued_count: number;
  running_count: number;
  passed_count: number;
  failed_count: number;
  error_count: number;
  cancelled_count: number;
  not_run_count: number;
  average_score: number | null;
  started_at?: string | null;
  completed_at?: string | null;
  runs: TestRun[];
}

export interface WebCallSessionPayload {
  agent_id: string;
  agent_version_number: number;
  dynamic_variables?: Record<string, unknown>;
  initial_user_utterance?: string | null;
  evaluation_rules?: InlineEvaluationRuleSpec[];
  allow_mock_fallback?: boolean;
  require_live_webrtc?: boolean;
}

export interface WebCallSessionEventPayload {
  event: 'start' | 'stop' | 'interrupt' | 'user_utterance' | 'dtmf' | 'complete';
  text?: string;
  dtmf_digits?: string;
  metadata?: Record<string, unknown>;
}

export interface PhoneCallTestPayload {
  agent_id: string;
  agent_version_number: number;
  to_number: string;
  from_number?: string;
  dynamic_variables?: Record<string, unknown>;
  scripted_user_turns?: string[];
  evaluation_rules?: InlineEvaluationRuleSpec[];
  require_live_carrier?: boolean;
}

export interface CallTestReadiness {
  tenant_id: string;
  webrtc_live_configured: boolean;
  webrtc_mode: string;
  telephony_carrier_configured: boolean;
  telephony_mode: string;
}

export interface RunQAScorecardDetail {
  test_run_id: string;
  tenant_id: string;
  suite_id?: string | null;
  test_case_id?: string | null;
  agent_id: string;
  agent_kind: AgentKind;
  agent_version_id?: string | null;
  agent_version_number: number;
  agent_config_hash: string;
  mode: TestRunMode;
  run_status: TestRunStatus;
  is_mock_provider: boolean;
  provider: string;
  model: string;
  scorecard: QAScorecardSummary;
  results: EvaluationResult[];
  transcript_turn_count: number;
  latency_metadata: Record<string, unknown>;
  usage_metadata: Record<string, unknown>;
  error_code?: string | null;
  error_message?: string | null;
  created_at?: string | null;
  completed_at?: string | null;
}

export interface AgentVersionQASummary {
  agent_id: string;
  agent_version_number?: number | null;
  formula_version: string;
  evaluator_version: string;
  total_runs: number;
  evaluated_runs_with_assertions: number;
  passed_runs: number;
  failed_assertion_runs: number;
  error_runs: number;
  no_assertion_runs: number;
  not_run_count: number;
  pass_rate_pct: number | null;
  average_score: number | null;
  average_turn_latency_ms: number | null;
}

export interface AgentVersionQAComparison {
  agent_id: string;
  version_a: AgentVersionQASummary;
  version_b: AgentVersionQASummary;
  score_delta: number | null;
  pass_rate_delta_pct: number | null;
}
