/**
 * dashboard/src/lib/telephonyApi.ts
 * Typed API client for Prompt 6 — Telephony / Voice Runtime:
 * Phone Numbers, SIP Connections, Outbound/Inbound Call Sessions,
 * Transfers, DTMF, Real-Time Media Events, Health, and Usage Ledger.
 */

import { client } from '../api/client';

export type TelephonyProviderName = 'TWILIO' | 'TELNYX' | 'VONAGE' | 'SIP' | 'SIMULATED';

export type PhoneNumberLifecycleStatus =
  | 'NOT_CONFIGURED'
  | 'CONFIGURED'
  | 'PROVISIONING'
  | 'READY'
  | 'ACTIVE'
  | 'SUSPENDED'
  | 'DISCONNECTED'
  | 'FAILED';

export type SipConnectionStatus =
  | 'NOT_CONFIGURED'
  | 'CONFIGURED'
  | 'PROVISIONING'
  | 'TESTING'
  | 'READY'
  | 'FAILED'
  | 'DISABLED';

export type SipTransportProtocol = 'UDP' | 'TCP' | 'TLS' | 'WSS';

export type TelephonyCallState =
  | 'NOT_CONFIGURED'
  | 'CONFIGURED'
  | 'PROVISIONING'
  | 'READY'
  | 'CREATED'
  | 'DIALING'
  | 'RINGING'
  | 'ANSWERED'
  | 'IN_PROGRESS'
  | 'TRANSFERRING'
  | 'TRANSFERRED'
  | 'ENDING'
  | 'COMPLETED'
  | 'FAILED'
  | 'CANCELLED'
  | 'BUSY'
  | 'NO_ANSWER'
  | 'VOICEMAIL';

export type TransferMode = 'COLD' | 'WARM' | 'AGENT_TO_AGENT';
export type TransferFallbackAction = 'RETURN_TO_AGENT' | 'RETRY' | 'HANGUP';

export interface PhoneNumberRecord {
  id: string;
  organization_id: string;
  environment_id: string | null;
  number: string;
  e164_number: string;
  provider: string;
  provider_number_id: string | null;
  sip_connection_id: string | null;
  sip_enabled: boolean;
  inbound_enabled: boolean;
  outbound_enabled: boolean;
  inbound_agent_id: string | null;
  outbound_agent_id: string | null;
  status: PhoneNumberLifecycleStatus | string;
  metadata: Record<string, unknown>;
  last_health_check_at: string | null;
  last_health_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface PhoneNumberListResponse {
  items: PhoneNumberRecord[];
  total: number;
}

export interface PhoneNumberCreatePayload {
  number: string;
  provider?: TelephonyProviderName;
  provider_number_id?: string | null;
  sip_connection_id?: string | null;
  sip_enabled?: boolean;
  inbound_enabled?: boolean;
  outbound_enabled?: boolean;
  inbound_agent_id?: string | null;
  outbound_agent_id?: string | null;
  metadata?: Record<string, unknown>;
}

export interface PhoneNumberUpdatePayload {
  provider?: TelephonyProviderName;
  provider_number_id?: string | null;
  sip_connection_id?: string | null;
  sip_enabled?: boolean;
  inbound_enabled?: boolean;
  outbound_enabled?: boolean;
  inbound_agent_id?: string | null;
  outbound_agent_id?: string | null;
  status?: PhoneNumberLifecycleStatus;
  metadata?: Record<string, unknown>;
}

export interface PhoneNumberBindAgentPayload {
  inbound_agent_id?: string | null;
  outbound_agent_id?: string | null;
  inbound_enabled?: boolean;
  outbound_enabled?: boolean;
}

export interface SipConnectionRecord {
  id: string;
  organization_id: string;
  environment_id: string | null;
  name: string;
  provider: string;
  phone_number_e164: string | null;
  termination_uri: string;
  origination_uri: string | null;
  username: string | null;
  credential_reference: string | null;
  has_credentials: boolean;
  transport: SipTransportProtocol | string;
  status: SipConnectionStatus | string;
  last_test_at: string | null;
  last_error: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface SipConnectionListResponse {
  items: SipConnectionRecord[];
  total: number;
}

export interface SipConnectionCreatePayload {
  name: string;
  provider?: TelephonyProviderName;
  phone_number?: string | null;
  termination_uri: string;
  origination_uri?: string | null;
  username?: string | null;
  password_secret?: string | null;
  credential_reference?: string | null;
  transport?: SipTransportProtocol;
  metadata?: Record<string, unknown>;
}

export interface SipConnectionTestPayload {
  termination_uri?: string;
  origination_uri?: string;
  transport?: SipTransportProtocol;
  simulate_unreachable?: boolean;
}

export interface CallTranscriptTurn {
  turn_index: number;
  role: string;
  content: string;
  timestamp: string;
  agent_id?: string | null;
  metadata?: Record<string, unknown>;
}

export interface CallRuntimeEvent {
  event_id: string;
  event_type: string;
  state: string;
  timestamp: string;
  detail: Record<string, unknown>;
}

export interface CallSessionRecord {
  id: string;
  organization_id: string;
  environment_id: string | null;
  legacy_call_id: string | null;
  phone_number_id: string | null;
  agent_id: string | null;
  agent_version_number: number | null;
  provider: string;
  provider_call_id: string;
  direction: 'INBOUND' | 'OUTBOUND' | string;
  from_number: string;
  to_number: string;
  status: TelephonyCallState | string;
  media_state: string;
  transfer_state: string;
  transfer_mode: string | null;
  transfer_target: string | null;
  transferred_to_agent_id: string | null;
  parent_call_id: string | null;
  is_simulation: boolean;
  execution_kind: string;
  started_at: string | null;
  answered_at: string | null;
  ended_at: string | null;
  duration_ms: number;
  billable_seconds: number;
  usage_finalized: boolean;
  hangup_reason: string | null;
  recording_reference: string | null;
  transcript_reference: string | null;
  transcript_turns: CallTranscriptTurn[];
  dtmf_buffer: string;
  dtmf_events: Array<Record<string, unknown>>;
  runtime_events: CallRuntimeEvent[];
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface CallSessionListResponse {
  items: CallSessionRecord[];
  total: number;
}

export interface OutboundCallCreatePayload {
  to_number: string;
  from_number?: string | null;
  phone_number_id?: string | null;
  agent_id?: string | null;
  provider?: TelephonyProviderName;
  idempotency_key?: string | null;
  is_simulation?: boolean;
  metadata?: Record<string, unknown>;
}

export interface CallDtmfPayload {
  digits: string;
  source?: string;
}

export interface CallDtmfResponse {
  call_id: string;
  accepted_digits: string;
  dtmf_buffer: string;
  matched_route: Record<string, unknown> | null;
  barge_in_triggered: boolean;
  status: string;
}

export interface CallTransferPayload {
  mode: TransferMode;
  target_destination?: string | null;
  target_agent_id?: string | null;
  whisper_message?: string | null;
  fallback_action?: TransferFallbackAction;
  reason?: string | null;
  idempotency_key?: string | null;
  simulate_target_failure?: boolean;
  context_overrides?: Record<string, unknown>;
}

export interface CallTransferRecord {
  id: string;
  call_session_id: string;
  organization_id: string;
  transfer_mode: string;
  source_agent_id: string | null;
  target_agent_id: string | null;
  target_destination: string;
  whisper_message: string | null;
  fallback_action: string;
  status: string;
  reason: string | null;
  failure_reason: string | null;
  context_snapshot: Record<string, unknown>;
  requested_at: string;
  completed_at: string | null;
}

export interface ProviderRuntimeStatus {
  provider: string;
  configured: boolean;
  state: string;
  has_credentials: boolean;
  webhook_verification_enabled: boolean;
  supported_capabilities: string[];
  message: string;
}

export interface TelephonyHealthStatus {
  state: string;
  default_provider: string;
  configured_phone_numbers: number;
  ready_phone_numbers: number;
  configured_sip_connections: number;
  ready_sip_connections: number;
  active_calls: number;
  providers: ProviderRuntimeStatus[];
  checked_at: string;
}

export interface TelephonyUsageSummary {
  organization_id: string;
  call_count: number;
  total_duration_ms: number;
  total_billable_seconds: number;
  total_billable_minutes: number;
}

// ---------------------------------------------------------------------------
// Phone Number API calls
// ---------------------------------------------------------------------------

export async function listPhoneNumbers(status?: string): Promise<PhoneNumberListResponse> {
  const query = status ? `?status=${encodeURIComponent(status)}` : '';
  return client.get<PhoneNumberListResponse>(`/api/v1/telephony/phone-numbers${query}`);
}

export async function createPhoneNumber(
  payload: PhoneNumberCreatePayload
): Promise<PhoneNumberRecord> {
  return client.post<PhoneNumberRecord>('/api/v1/telephony/phone-numbers', payload);
}

export async function getPhoneNumber(phoneNumberId: string): Promise<PhoneNumberRecord> {
  return client.get<PhoneNumberRecord>(
    `/api/v1/telephony/phone-numbers/${encodeURIComponent(phoneNumberId)}`
  );
}

export async function updatePhoneNumber(
  phoneNumberId: string,
  payload: PhoneNumberUpdatePayload
): Promise<PhoneNumberRecord> {
  return client.patch<PhoneNumberRecord>(
    `/api/v1/telephony/phone-numbers/${encodeURIComponent(phoneNumberId)}`,
    payload
  );
}

export async function deletePhoneNumber(
  phoneNumberId: string
): Promise<{ deleted: boolean; id: string; e164_number: string }> {
  return client.delete<{ deleted: boolean; id: string; e164_number: string }>(
    `/api/v1/telephony/phone-numbers/${encodeURIComponent(phoneNumberId)}`
  );
}

export async function bindPhoneNumberAgent(
  phoneNumberId: string,
  payload: PhoneNumberBindAgentPayload
): Promise<PhoneNumberRecord> {
  return client.post<PhoneNumberRecord>(
    `/api/v1/telephony/phone-numbers/${encodeURIComponent(phoneNumberId)}/bind-agent`,
    payload
  );
}

// ---------------------------------------------------------------------------
// SIP Connection API calls
// ---------------------------------------------------------------------------

export async function listSipConnections(): Promise<SipConnectionListResponse> {
  return client.get<SipConnectionListResponse>('/api/v1/telephony/sip-connections');
}

export async function createSipConnection(
  payload: SipConnectionCreatePayload
): Promise<SipConnectionRecord> {
  return client.post<SipConnectionRecord>('/api/v1/telephony/sip-connections', payload);
}

export async function testSipConnection(
  sipConnectionId: string,
  payload: SipConnectionTestPayload = {}
): Promise<SipConnectionRecord> {
  return client.post<SipConnectionRecord>(
    `/api/v1/telephony/sip-connections/${encodeURIComponent(sipConnectionId)}/test`,
    payload
  );
}

// ---------------------------------------------------------------------------
// Call Session & Control API calls
// ---------------------------------------------------------------------------

export async function listTelephonyCalls(params?: {
  status?: string;
  direction?: string;
  agent_id?: string;
  limit?: number;
}): Promise<CallSessionListResponse> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set('status', params.status);
  if (params?.direction) qs.set('direction', params.direction);
  if (params?.agent_id) qs.set('agent_id', params.agent_id);
  if (params?.limit) qs.set('limit', String(params.limit));
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  return client.get<CallSessionListResponse>(`/api/v1/telephony/calls${suffix}`);
}

export async function getTelephonyCall(callId: string): Promise<CallSessionRecord> {
  return client.get<CallSessionRecord>(`/api/v1/telephony/calls/${encodeURIComponent(callId)}`);
}

export async function createOutboundCall(
  payload: OutboundCallCreatePayload
): Promise<CallSessionRecord> {
  return client.post<CallSessionRecord>('/api/v1/telephony/calls/outbound', payload);
}

export async function hangupTelephonyCall(
  callId: string,
  reason = 'operator_hangup'
): Promise<CallSessionRecord> {
  return client.post<CallSessionRecord>(
    `/api/v1/telephony/calls/${encodeURIComponent(callId)}/hangup`,
    { reason }
  );
}

export async function sendTelephonyCallDtmf(
  callId: string,
  payload: CallDtmfPayload
): Promise<CallDtmfResponse> {
  return client.post<CallDtmfResponse>(
    `/api/v1/telephony/calls/${encodeURIComponent(callId)}/dtmf`,
    payload
  );
}

export async function transferTelephonyCall(
  callId: string,
  payload: CallTransferPayload
): Promise<CallTransferRecord> {
  return client.post<CallTransferRecord>(
    `/api/v1/telephony/calls/${encodeURIComponent(callId)}/transfer`,
    payload
  );
}

export async function listTelephonyCallTransfers(
  callId: string
): Promise<CallTransferRecord[]> {
  return client.get<CallTransferRecord[]>(
    `/api/v1/telephony/calls/${encodeURIComponent(callId)}/transfers`
  );
}

export async function postTelephonyCallMediaEvent(
  callId: string,
  message: Record<string, unknown>
): Promise<Record<string, unknown>> {
  return client.post<Record<string, unknown>>(
    `/api/v1/telephony/calls/${encodeURIComponent(callId)}/media-event`,
    message
  );
}

// ---------------------------------------------------------------------------
// Health & Usage API calls
// ---------------------------------------------------------------------------

export async function getTelephonyHealth(): Promise<TelephonyHealthStatus> {
  return client.get<TelephonyHealthStatus>('/api/v1/telephony/health');
}

export async function getTelephonyUsage(
  includeSimulations = false
): Promise<TelephonyUsageSummary> {
  return client.get<TelephonyUsageSummary>(
    `/api/v1/telephony/usage?include_simulations=${includeSimulations ? 'true' : 'false'}`
  );
}
