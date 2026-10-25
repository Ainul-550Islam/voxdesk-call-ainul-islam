export type CallStatus =
  | "pending"
  | "dialing"
  | "waiting_for_agent"
  | "in_progress"
  | "held"
  | "completed"
  | "failed"
  | "cancelled"
  | "transferred";

export interface IceServer {
  urls: string[] | string;
  username?: string;
  credential?: string;
}

export interface SoftphoneSession {
  id: string;
  user_id: string;
  environment_id: string;
  client_identity: string;
  identity: string;
  device_label: string;
  status: "active" | "expired" | "revoked";
  expires_at: string;
  ice_servers: IceServer[];
  token: string;
  token_ttl_seconds: number;
  device_id?: string | null;
  websocket_ticket?: string;
  signaling_url?: string;
}

export interface SoftphoneLeg {
  id: string;
  kind: "customer" | "agent" | "consult";
  status: string;
  destination_redacted: string;
  answered_at: string | null;
  ended_at: string | null;
}

export interface WarmTransferBriefing {
  text_redacted: string;
  status: string;
  human_briefed: boolean;
  ai_detached: boolean;
  returned_to_ai: boolean;
}

export interface CustomerContext {
  lead: { id: string; name: string; company: string | null; score: number | null; status: string } | null;
  prior_calls: Array<{ id: string; status: string; started_at: string; duration_seconds: number; summary_redacted: string }>;
  crm: Record<string, string | number | boolean>;
  crm_synced?: boolean;
}

export interface SoftphoneCall {
  id: string;
  call_id: string | null;
  session_id: string | null;
  origin: "agent_outbound" | "acd" | "dialer" | "callback";
  status: CallStatus;
  agent_joined_at: string | null;
  answered_at: string | null;
  ended_at: string | null;
  failure_category: string;
  warm_transfer_briefing?: WarmTransferBriefing | null;
  customer_context?: CustomerContext;
  recording_paused?: boolean | null;
  muted: boolean;
  on_hold: boolean;
  created_at: string;
  legs: SoftphoneLeg[];
}

export interface AgentOccupancy {
  user_id: string;
  seconds: Record<string, number>;
  occupancy: string | null;
  occupancy_percent: string | null;
}

export interface CallbackRequest {
  id: string;
  queue_entry_id: string;
  queue_id: string;
  call_id: string;
  requested_by: string | null;
  assigned_agent_id: string | null;
  status: string;
  scheduled_at: string;
  next_attempt_at: string;
  attempts: number;
  timezone: string;
  note: string;
  failure_category: string;
}

export interface AssistTurn {
  id: string;
  seq: number;
  speaker: "caller" | "agent" | "system" | "supervisor";
  text_redacted: string;
  processing_status: string;
  sentiment_score?: string | null;
  sentiment_label?: string;
  sentiment_method?: string;
  created_at: string;
}

export interface AssistSuggestion {
  id: string;
  kind: "knowledge" | "next_best_action";
  text: string;
  citations: Array<Record<string, unknown>>;
  generation_method: string;
  model?: string;
  feedback?: string;
  feedback_note?: string;
  created_at: string;
}

export interface AssistAlert {
  id: string;
  kind: string;
  severity: string;
  message: string;
  evidence: Record<string, unknown>;
  status: string;
  created_at: string;
}

export interface WrapupDraft {
  id: string;
  call_id: string;
  status: "draft" | "submitted" | "failed";
  summary_redacted: string;
  disposition: string;
  tags: string[];
  deadline_at: string;
  crm_status?: "not_requested" | "pending" | "written" | "failed";
  crm_reference?: string;
}

export interface WallboardQueue {
  queue_id: string;
  name: string;
  enabled: boolean;
  waiting: number;
  assigned: number;
  oldest_wait_seconds: number;
  estimated_wait_seconds?: number;
  estimate_method?: string;
  priority?: number;
  answered?: number;
  abandoned?: number;
  offered?: number;
  service_level_percent?: number | null;
  service_level_threshold_seconds?: number;
  abandon_percent?: number | null;
  asa_seconds?: number | null;
  aht_seconds?: number | null;
  aht_sample_size?: number;
}

export interface WallboardAgent {
  user_id: string;
  full_name: string;
  email: string;
  state: string;
  active_count: number;
  capacity: number;
  softphone_registered: boolean;
}

export interface ComplianceScript {
  id: string;
  environment_id: string;
  name: string;
  version: number;
  jurisdiction: string;
  script_text: string;
  required_phrases: string[];
  forbidden_phrases: string[];
  active: boolean;
}

export interface ComplianceAdherence {
  id: string;
  script_id: string;
  status: "pending" | "met" | "missed" | "violated";
  matched_phrases: string[];
  missed_phrases: string[];
  violated_phrases: string[];
  updated_at: string;
}

export interface WallboardSnapshot {
  generated_at: string;
  streamed_at?: string;
  source?: string;
  queues: WallboardQueue[];
  agents: WallboardAgent[];
  callbacks: Record<string, number>;
  assist: { active_sessions: number; open_alerts: number };
}
