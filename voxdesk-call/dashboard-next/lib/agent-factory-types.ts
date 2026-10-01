// Agent Factory — Comprehensive TypeScript Types for All Workspaces
// P0-02 Specialized Agents, P0-03 Workflow Builder, P0-04 Voice, P0-05 Governance,
// P1-01 Connectors, P1-03 QMS, P1-04 Legal, P1-05 Translation, P1 Intelligence
// Backend API: FastAPI — app/api/* routes, all connected via api.ts

// ============================================================================
// Workflow Builder — P0-03 Full Canvas E2E
// Backend: app/builder/workflow_repository.py, workflow_executor.py, workflow_state.py, orchestration/workflow.py, conditions.py, workflow_routes.py 15 routes
// ============================================================================

export type WorkflowNodeType = "trigger" | "ai_agent" | "condition" | "action" | "delay" | "approval" | "integration";
export type WorkflowStatus = "draft" | "published" | "paused" | "archived";

export interface WorkflowCanvasNode {
  id: string;
  type: WorkflowNodeType;
  x: number;
  y: number;
  label: string;
  config: Record<string, unknown>;
  condition?: string;
  position?: { x: number; y: number };
  data?: Record<string, unknown>;
}

export interface WorkflowCanvasEdge {
  id: string;
  from: string;
  to: string;
  source?: string;
  target?: string;
  label?: string;
  condition?: string;
}

export interface WorkflowDefinitionFull {
  id: string;
  tenant_id: string;
  organization_id?: string;
  environment_id?: string;
  name: string;
  description?: string;
  status: WorkflowStatus;
  version: number;
  nodes: WorkflowCanvasNode[];
  edges: WorkflowCanvasEdge[];
  created_at: string;
  updated_at: string;
  published_at?: string | null;
  created_by?: string;
}

export interface WorkflowExecutionFull {
  id: string;
  workflow_id: string;
  tenant_id?: string;
  status: string;
  current_node?: string;
  context: Record<string, unknown>;
  result?: Record<string, unknown>;
  error?: string | null;
  started_at?: string;
  completed_at?: string | null;
  created_at: string;
  updated_at: string;
  triggered_by?: string;
}

export interface WorkflowCreatePayload {
  name: string;
  description?: string;
  nodes: Array<{ id: string; type: string; label: string; position: { x: number; y: number }; config: Record<string, unknown>; condition?: string }>;
  edges: Array<{ id: string; source: string; target: string; label?: string; condition?: string }>;
  environment_id: string;
}

// ============================================================================
// Voice Agent — P0-04 Design→Voice→Connect→Launch
// Backend: app/voice/provider_registry.py, voice_profile_service.py, clone.py, clone_worker.py, telephony/ivr.py, transfer.py, phone.py, tts/*, stt.py, 23 endpoints
// ============================================================================

export type VoiceStep = "design" | "voice" | "connect" | "launch";
export type IVRNodeType = "greeting" | "menu" | "input" | "transfer" | "hangup" | "ai";

export interface IVRNode {
  id: string;
  type: IVRNodeType;
  prompt: string;
  options?: { dtmf: string; next: string; label: string }[];
  next?: string;
}

export interface VoiceProfileFull {
  id: string;
  tenant_id: string;
  organization_id?: string;
  name: string;
  provider: string;
  provider_voice_id: string;
  language: string;
  status: string;
  settings?: Record<string, unknown>;
  created_at: string;
  updated_at?: string;
}

export interface VoiceCloneJobFull {
  id: string;
  tenant_id: string;
  name: string;
  provider: string;
  status: "pending" | "processing" | "completed" | "failed" | "cancelled";
  audio_url?: string | null;
  description?: string | null;
  result_voice_id?: string | null;
  error?: string | null;
  created_at: string;
  updated_at: string;
}

export interface VoiceConfig {
  provider: string;
  voice_id: string;
  language: string;
  speed: number;
  humanize: boolean;
  filler_words: boolean;
  stt_provider: string;
  stt_model: string;
  llm_provider: string;
  llm_model: string;
}

export interface VoiceCloneCreatePayload {
  name: string;
  provider: string;
  audio_url?: string;
  description?: string;
}

// ============================================================================
// Translation — P1-05 Side-by-Side Review, Glossary, Audit Chain, Batch
// Backend: app/translation/engine.py, glossary.py, job_service.py, persistence.py, quality.py, schemas.py, 2 routes + specialized agent
// ============================================================================

export type TranslationSegmentStatus = "pending" | "translated" | "reviewed" | "approved" | "rejected";

export interface TranslationSegment {
  id: string;
  segment_id?: string;
  source: string;
  source_text?: string;
  target: string;
  translated_text?: string;
  status: TranslationSegmentStatus;
  quality_flags: string[];
  source_fingerprint: string;
  target_fingerprint: string;
  glossary_terms: string[];
  quality_status?: string;
  review_state?: string;
}

export interface GlossaryEntry {
  term: string;
  translation: string;
  case_sensitive: boolean;
  category?: string;
}

export interface GlossaryVersion {
  id: string;
  version: string;
  entries: GlossaryEntry[];
  fingerprint: string;
  created_at: string;
  tenant_id?: string;
}

export interface TranslationJob {
  id: string;
  tenant_id: string;
  source_language: string;
  target_language: string;
  glossary_version?: string;
  status: string;
  provider?: string;
  model?: string;
  progress?: { total: number; done: number; failed: number };
  created_at: string;
  updated_at?: string;
}

export interface TranslationExecutePayload {
  environment_id: string;
  payload: {
    source_language: string;
    target_language: string;
    segments: Array<{ segment_id: string; source_text: string }>;
    glossary_version?: string;
    glossary?: GlossaryEntry[];
  };
  source_references: Array<{
    document_id?: string;
    chunk_id?: string;
    content_fingerprint: string;
  }>;
}

// ============================================================================
// Forecasting — P1 Confidence Intervals, Scenarios, Backtesting
// Backend: app/forecasting/service.py analyze(), app/analytics/forecast.py project_usage(), forecast_routes.py 2 routes
// ============================================================================

export interface UsagePoint {
  period: string;
  value: number;
}

export interface ForecastProjection {
  step: number;
  period: string;
  label?: string;
  value: number;
  lower: number | null;
  upper: number | null;
  interval_status: "available" | "NOT_AVAILABLE";
}

export interface ForecastScenario {
  id: string;
  name: string;
  method: string;
  horizon: number;
  window: number;
  alpha: number;
  points: UsagePoint[];
  projections: ForecastProjection[];
  interval_method: string;
  data_quality: string;
  review_required: boolean;
  created_at?: string;
}

export interface ForecastBacktestResult {
  mape: number;
  rmse: number;
  method: string;
  points: number;
  train_points?: number;
  test_points?: number;
}

export interface ForecastingExecutePayload {
  environment_id: string;
  payload: {
    points: UsagePoint[];
    method: string;
    horizon: number;
    window?: number;
    alpha?: number;
    series_reference?: string;
  };
  source_references: Array<{ content_fingerprint: string }>;
}

export interface ForecastingResponse {
  method: string;
  method_metadata: { implementation: string; version: string };
  series_reference: string;
  series_fingerprint: string;
  horizon: number;
  projections: ForecastProjection[];
  interval_method: string;
  data_quality: string;
  observation_count: number;
  residual_error: { status: string; method: string | null };
  review_required: boolean;
  review_state: string;
  disclaimer: string;
}

// ============================================================================
// Insight — P1 Cited Institutional Memory, Source Explorer
// Backend: app/insight/service.py generate(), resolve_sources(), analyze(), knowledge/retrieval.py tenant-bound, 2 routes + specialized agent
// ============================================================================

export interface SourceReference {
  document_id: string;
  chunk_id: string;
  title: string;
  source_title?: string;
  content_fingerprint: string;
  fingerprint?: string;
  page?: number;
  page_number?: number;
  excerpt: string;
  text?: string;
  score: number;
  retrieval_timestamp?: string;
}

export type InsightKind = "observed_fact" | "derived_metric" | "model_interpretation" | "recommendation_for_review";

export interface InsightItem {
  kind: InsightKind;
  statement: string;
  source_references: Array<{ content_fingerprint: string }>;
  statement_fingerprint: string;
  review_required: boolean;
}

export interface InsightMetric {
  kind: string;
  value: number;
  unit?: string;
}

export interface InsightExecutePayload {
  environment_id: string;
  payload: {
    question: string;
    metrics: InsightMetric[];
    document_ids?: string[];
    time_range?: { start?: string; end?: string };
    analysis_type?: string;
  };
  source_references: Array<{
    document_id: string;
    chunk_id: string;
    content_fingerprint: string;
  }>;
}

export interface InsightResponse {
  items: InsightItem[];
  review_required: boolean;
  review_state: string;
  source_fingerprints: string[];
  disclaimer: string;
}

// ============================================================================
// Anomaly Detection — P1 Real-time, Tunable Sensitivity, Explained Routing
// Backend: app/anomaly/detectors.py z_score_detect(), persistence.py AnomalyRunRecord, alerting.py publish_alerts, schemas.py, service.py analyze(), 2 routes + specialized agent
// ============================================================================

export interface AnomalyObservation {
  timestamp: string;
  value: number;
}

export interface AnomalyDetectorConfig {
  method: string;
  sensitivity: number;
  window: number;
}

export interface AnomalyDetection {
  id: string;
  metric: string;
  model?: string;
  result: string;
  status?: string;
  confidence: number;
  severity: "low" | "medium" | "high" | "critical";
  observed_value: number;
  value?: number;
  expected_range: [number, number];
  created_at: string;
  config: AnomalyDetectorConfig;
  explanation: string;
  routing: string;
}

export interface AnomalyExecutePayload {
  environment_id: string;
  payload: {
    metric: string;
    observations: AnomalyObservation[];
    configuration: AnomalyDetectorConfig;
  };
  source_references: Array<{ content_fingerprint: string }>;
}

// ============================================================================
// QMS — P1-03 Veeva Vault, MasterControl, ETQ Native Adapters
// Backend: app/compliance/qms_adapters.py VeevaVaultAdapter/MasterControlAdapter/ETQAdapter, qms.py evaluate_control, compliance_routes.py 15+ routes
// ============================================================================

export interface QMSHealthResult {
  provider: string;
  connected: boolean;
  latency_ms: number;
  message: string;
  safe_message?: string;
  details?: Record<string, unknown>;
  external_blocker?: boolean;
}

export interface QMSDocument {
  external_id: string;
  title: string;
  status: string;
  revision?: string | null;
  owner?: string | null;
  effective_date?: string | null;
  approval_state?: string | null;
  last_reviewed_at?: string | null;
  url?: string | null;
  metadata?: Record<string, unknown>;
}

export interface QMSTraceability {
  subject_type: string;
  subject_id: string;
  provider: string;
  tenant_id: string;
  traceability_graph: Array<Record<string, unknown>>;
  note?: string;
}

export interface QMSAuditPackage {
  framework_id: string;
  provider: string;
  tenant_id: string;
  documents: QMSDocument[];
  findings: Array<Record<string, unknown>>;
  traceability: Record<string, unknown>;
  note?: string;
}

export interface QMSProvidersResponse {
  providers: string[];
  note: string;
}

// ============================================================================
// Legal — P1-04 Clause Library, Playbook, Redline Artifacts
// Backend: app/legal/playbook.py, redline.py, clause_library.py, clause_engine.py PATTERNS, legal_routes.py 10 routes
// ============================================================================

export interface ClauseLibraryEntry {
  key: string;
  title: string;
  category: string;
  risk_tier: string;
  version: string;
  pattern?: string;
  suggested_text?: string;
  fallback_text?: string;
  source?: string;
  review_required?: boolean;
  fingerprint?: string;
}

export interface PlaybookRule {
  id: string;
  clause_key: string;
  operator: "contains" | "equals" | "gte" | "lte";
  value: string;
  severity: string;
  action: string;
  fingerprint: string;
}

export interface Playbook {
  id: string;
  tenant_id: string;
  organization_id: string;
  environment_id: string;
  name: string;
  version: number;
  status: "draft" | "active" | "archived";
  clauses: ClauseLibraryEntry[];
  rules: PlaybookRule[];
  created_at: string;
  updated_at: string;
}

export interface RedlineChange {
  change_type: "insertion" | "deletion" | "replacement";
  original_text: string;
  suggested_text: string;
  rationale: string;
  clause_key: string;
  source_reference?: string;
  evidence_reference?: string;
  fingerprint: string;
}

export interface RedlineArtifact {
  id: string;
  document_id: string;
  playbook_id: string;
  changes: RedlineChange[];
  diff_html: string;
  fingerprint: string;
  disclaimer: string;
  review_state: "pending" | "approved" | "rejected";
  evidence_references: string[];
  created_at: string;
}

export interface PlaybookEvaluateResponse {
  playbook_id: string;
  playbook_version: number;
  finding_count: number;
  matched_count: number;
  evaluations: Array<{
    finding: Record<string, unknown>;
    clause: ClauseLibraryEntry;
    rule: PlaybookRule;
    review_required: boolean;
  }>;
  disclaimer: string;
}

export interface RedlineCreateResponse {
  document_id: string;
  playbook_id: string;
  playbook_version: number;
  artifact_count: number;
  artifacts: RedlineArtifact[];
  disclaimer: string;
  review_required: boolean;
}

// ============================================================================
// Connectors — P1-01 21 Providers (4 CRM +5 Calendar +12 Enterprise)
// Backend: app/integrations/connector.py 21 providers, connector_routes.py
// ============================================================================

export interface ConnectorProvider {
  provider: string;
  is_enabled: boolean;
  connected: boolean;
  capabilities: string[];
  config: Record<string, unknown>;
  field_mappings: Record<string, string>;
  subscribed_events: string[];
  share_transcripts: boolean;
  connected_at: string | null;
  last_health_check_at: string | null;
  last_health_ok: boolean | null;
  last_error: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ConnectorHealth {
  provider: string;
  connected: boolean;
  latency_ms?: number;
  message?: string;
  safe_message?: string;
  details?: Record<string, unknown>;
}

// ============================================================================
// Specialized Agents — P0-02 Control Plane (16 Definitions)
// Backend: app/specialized_agents/registry.py 16 defs, specialized_agent_routes.py
// ============================================================================

export interface SpecializedAgentDefinitionFull {
  id: string;
  type: string;
  name: string;
  version: string;
  status: "active" | "retired" | "draft";
  risk_tier: "low" | "medium" | "high" | "critical";
  capabilities: string[];
  supported_inputs: string[];
  supported_outputs: string[];
  required_controls: string[];
  description?: string;
}

export interface SpecializedExecutionRequestFull {
  environment_id: string;
  agent_version?: string;
  model_version_id?: string;
  risk_tier?: string;
  locale?: string;
  language?: string;
  payload: Record<string, unknown>;
  source_references: Array<{
    document_id?: string;
    chunk_id?: string;
    source_title?: string;
    content_fingerprint: string;
    page_number?: number;
    character_start?: number;
    character_end?: number;
    retrieval_timestamp?: string;
  }>;
  idempotency_key?: string;
}

export interface SpecializedExecutionResponseFull {
  id: string;
  tenant_id: string;
  organization_id: string;
  environment_id: string;
  request_id: string;
  trace_id: string;
  agent_type: string;
  agent_version: string;
  model_version_id?: string;
  risk_tier: string;
  status: string;
  input_fingerprint: string;
  output_fingerprint: string;
  policy_decision_id?: string;
  lineage_root_id?: string;
  evidence_root_hash?: string;
  review_required: boolean;
  review_state: string;
  result: Record<string, unknown>;
  failure_code?: string;
  started_at?: string;
  completed_at?: string;
  created_at: string;
  updated_at: string;
}

// ============================================================================
// Governance — P0-05 Governance Center
// Backend: app/governance/, review/, evidence, risk, lineage, residency
// ============================================================================

export interface GovernancePolicyFull {
  id: string;
  tenant_id: string;
  organization_id: string;
  environment_id?: string;
  policy_type: string;
  name: string;
  description?: string;
  status: string;
  version: number;
  rules: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ReviewCaseFull {
  id: string;
  tenant_id: string;
  status: string;
  priority: string;
  subject_type: string;
  subject_id: string;
  assigned_to?: string;
  created_at: string;
  updated_at: string;
  review_state?: string;
}

export interface EvidenceRecordFull {
  id: string;
  tenant_id: string;
  event_type: string;
  payload_hash: string;
  previous_hash?: string;
  created_at: string;
  payload?: Record<string, unknown>;
}

// ============================================================================
// Backend API Connection — Type-Safe API Client
// ============================================================================

export interface ApiConnectionStatus {
  connected: boolean;
  latency_ms?: number;
  base_url: string;
  last_check: string;
  error?: string | null;
}

export interface BackendHealth {
  status: string;
  version: string;
  environment: string;
  database: string;
  redis?: string;
  timestamp: string;
}
