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

export const callsApi = {
  getCallTestReadiness,
  listCallTestRuns,
  getCallTestRun,
  startWebCallSession,
  sendWebCallEvent,
  getWebCallSession,
  initiatePhoneCallTest,
  getPhoneCallTest,
};
