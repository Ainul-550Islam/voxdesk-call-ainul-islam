import { apiClient } from './client';
import type {
  CallTestReadiness,
  PhoneCallTestPayload,
  TestRun,
  WebCallSessionEventPayload,
  WebCallSessionPayload,
} from '../types/evaluation';

export async function getCallTestReadiness(): Promise<CallTestReadiness> {
  return apiClient.get<CallTestReadiness>('/api/v1/testing/calls/readiness');
}

export async function listCallTestRuns(params?: {
  mode?: 'web_call' | 'phone_call';
  agent_id?: string;
  limit?: number;
}): Promise<TestRun[]> {
  const qs = new URLSearchParams();
  if (params?.mode) qs.set('mode', params.mode);
  if (params?.agent_id) qs.set('agent_id', params.agent_id);
  if (params?.limit !== undefined) qs.set('limit', String(params.limit));
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  const res = await apiClient.get<TestRun[]>(
    `/api/v1/testing/calls/runs${suffix}`
  );
  return Array.isArray(res) ? res : [];
}

export async function getCallTestRun(runId: string): Promise<TestRun> {
  return apiClient.get<TestRun>(
    `/api/v1/testing/calls/runs/${encodeURIComponent(runId)}`
  );
}

export async function startWebCallSession(
  payload: WebCallSessionPayload
): Promise<TestRun> {
  return apiClient.post<TestRun>('/api/v1/testing/web-calls/sessions', payload);
}

export async function sendWebCallEvent(
  runId: string,
  payload: WebCallSessionEventPayload
): Promise<TestRun> {
  return apiClient.post<TestRun>(
    `/api/v1/testing/web-calls/sessions/${encodeURIComponent(runId)}/events`,
    payload
  );
}

export async function getWebCallSession(runId: string): Promise<TestRun> {
  return apiClient.get<TestRun>(
    `/api/v1/testing/web-calls/sessions/${encodeURIComponent(runId)}`
  );
}

export async function initiatePhoneCallTest(
  payload: PhoneCallTestPayload
): Promise<TestRun> {
  return apiClient.post<TestRun>('/api/v1/testing/phone-calls/run', payload);
}

export const startPhoneCallTest = initiatePhoneCallTest;

export async function getPhoneCallTest(runId: string): Promise<TestRun> {
  return apiClient.get<TestRun>(
    `/api/v1/testing/phone-calls/${encodeURIComponent(runId)}`
  );
}

// ---------------------------------------------------------------------------
// Live call control
//
// Every function here performs a carrier side effect, so every one of them
// sends an `Idempotency-Key`. The server uses that key to store a durable
// receipt: if the request is retried after a timeout, the stored answer comes
// back instead of the caller being paused twice. Keys are generated per call,
// not per page, so a remount cannot accidentally reuse one.
// ---------------------------------------------------------------------------

export type CallControlAction =
  | 'pause'
  | 'resume'
  | 'dtmf'
  | 'transfer_cancel'
  | 'transfer_whisper';

export interface CallControlCapabilities {
  pause: boolean;
  resume: boolean;
  dtmf: boolean;
  transfer_cancel: boolean;
  whisper: boolean;
  bulk_transfer: boolean;
}

export interface CallPauseResponse {
  call_id: string;
  action: 'pause';
  status: string;
  provider: string;
  provider_status: string;
  reason: string | null;
  idempotency_key: string;
  replayed: boolean;
  capabilities: CallControlCapabilities;
  at: string;
}

export interface CallResumeResponse {
  call_id: string;
  action: 'resume';
  status: string;
  provider: string;
  provider_status: string;
  reason: string | null;
  idempotency_key: string;
  replayed: boolean;
  capabilities: CallControlCapabilities;
  at: string;
}

export interface DtmfItem {
  /** Digits for this run: `0-9`, `#`, `*`, `w`/`W` (wait for silence). */
  digits: string;
  idempotency_key?: string;
}

export interface DtmfItemResult {
  index: number;
  digits: string;
  status: 'sent' | 'replayed' | 'rejected' | 'failed' | 'pending';
  idempotency_key: string;
  replayed: boolean;
  error: string | null;
}

export interface DtmfBatchResponse {
  call_id: string;
  action: 'dtmf_batch';
  provider: string;
  items: DtmfItemResult[];
  sent: number;
  replayed: number;
  rejected: number;
  capabilities: CallControlCapabilities;
  at: string;
}

export interface TransferCancelResponse {
  call_id: string;
  action: 'transfer_cancel';
  transfer_state: string;
  provider: string;
  provider_status: string;
  reason: string | null;
  idempotency_key: string;
  replayed: boolean;
  capabilities: CallControlCapabilities;
  at: string;
}

export interface TransferWhisperResponse {
  call_id: string;
  action: 'transfer_whisper';
  transfer_state: string;
  provider: string;
  provider_status: string;
  /** The text that was spoken, after credentials and digit runs were removed. */
  message: string;
  idempotency_key: string;
  replayed: boolean;
  capabilities: CallControlCapabilities;
  at: string;
}

export interface BulkTransferItem {
  call_id: string;
  destination: string;
  reason?: string;
  idempotency_key?: string;
}

export interface BulkTransferItemResult {
  call_id: string;
  index: number;
  status: 'succeeded' | 'replayed' | 'failed' | 'rejected';
  transfer_state: string | null;
  destination: string | null;
  idempotency_key: string | null;
  replayed: boolean;
  error_code: string | null;
  error: string | null;
}

export interface BulkTransferResponse {
  batch_id: string;
  action: 'bulk_transfer';
  provider: string;
  items: BulkTransferItemResult[];
  total: number;
  succeeded: number;
  replayed: number;
  failed: number;
  capabilities: CallControlCapabilities;
  at: string;
}

/**
 * One idempotency key per side effect.
 *
 * `crypto.randomUUID` is used when it exists; the fallback is only for the
 * non-secure-context test environment, where a counter plus randomness is
 * enough because the value only needs to be unique per client, not secret.
 */
let keyCounter = 0;
export function newIdempotencyKey(): string {
  const cryptoObj: Crypto | undefined =
    typeof globalThis !== 'undefined'
      ? (globalThis.crypto as Crypto | undefined)
      : undefined;
  if (cryptoObj && typeof cryptoObj.randomUUID === 'function') {
    return cryptoObj.randomUUID();
  }
  keyCounter += 1;
  return `idem-${Date.now().toString(36)}-${keyCounter}-${Math.random()
    .toString(36)
    .slice(2, 10)}`;
}

function withIdempotencyKey(key?: string): Record<string, string> {
  return { 'Idempotency-Key': key && key.trim() ? key.trim() : newIdempotencyKey() };
}

export async function pauseCall(
  callId: string,
  payload: { reason?: string; hold_message?: string } = {},
  idempotencyKey?: string
): Promise<CallPauseResponse> {
  return apiClient.post<CallPauseResponse>(
    `/api/calls/${encodeURIComponent(callId)}/pause`,
    payload,
    { headers: withIdempotencyKey(idempotencyKey) }
  );
}

export async function resumeCall(
  callId: string,
  payload: { reason?: string } = {},
  idempotencyKey?: string
): Promise<CallResumeResponse> {
  return apiClient.post<CallResumeResponse>(
    `/api/calls/${encodeURIComponent(callId)}/resume`,
    payload,
    { headers: withIdempotencyKey(idempotencyKey) }
  );
}

export async function sendDtmfBatch(
  callId: string,
  items: DtmfItem[],
  idempotencyKey?: string
): Promise<DtmfBatchResponse> {
  // Each item carries its own key: a run is a separate carrier write, so a
  // batch-level key would make "retry the two that failed" impossible.
  const tagged = items.map((item, index) => ({
    ...item,
    idempotency_key:
      item.idempotency_key && item.idempotency_key.trim()
        ? item.idempotency_key
        : `${idempotencyKey && idempotencyKey.trim() ? idempotencyKey.trim() : newIdempotencyKey()}-${index}`,
  }));
  return apiClient.post<DtmfBatchResponse>(
    `/api/calls/${encodeURIComponent(callId)}/dtmf/batch`,
    { items: tagged },
    { headers: withIdempotencyKey(idempotencyKey) }
  );
}

export async function cancelTransfer(
  callId: string,
  payload: { reason?: string } = {},
  idempotencyKey?: string
): Promise<TransferCancelResponse> {
  return apiClient.post<TransferCancelResponse>(
    `/api/calls/${encodeURIComponent(callId)}/transfer/cancel`,
    payload,
    { headers: withIdempotencyKey(idempotencyKey) }
  );
}

export async function whisperTransfer(
  callId: string,
  message: string,
  idempotencyKey?: string
): Promise<TransferWhisperResponse> {
  return apiClient.post<TransferWhisperResponse>(
    `/api/calls/${encodeURIComponent(callId)}/transfer/whisper`,
    { message },
    { headers: withIdempotencyKey(idempotencyKey) }
  );
}

export async function bulkTransfer(
  items: BulkTransferItem[],
  idempotencyKey?: string
): Promise<BulkTransferResponse> {
  const key =
    idempotencyKey && idempotencyKey.trim()
      ? idempotencyKey.trim()
      : newIdempotencyKey();
  const tagged = items.map((item, index) => ({
    ...item,
    idempotency_key:
      item.idempotency_key && item.idempotency_key.trim()
        ? item.idempotency_key
        : `${key}-${index}`,
  }));
  return apiClient.post<BulkTransferResponse>(
    '/api/calls/transfers/bulk',
    { items: tagged, idempotency_key: key },
    { headers: withIdempotencyKey(key) }
  );
}

export const callControlsApi = {
  pauseCall,
  resumeCall,
  sendDtmfBatch,
  cancelTransfer,
  whisperTransfer,
  bulkTransfer,
};

export const callsApi = {
  getCallTestReadiness,
  listCallTestRuns,
  getCallTestRun,
  startWebCallSession,
  sendWebCallEvent,
  getWebCallSession,
  initiatePhoneCallTest,
  getPhoneCallTest,
  pauseCall,
  resumeCall,
  sendDtmfBatch,
  cancelTransfer,
  whisperTransfer,
  bulkTransfer,
};
