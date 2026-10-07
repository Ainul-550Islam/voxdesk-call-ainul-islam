import { apiClient } from './client';
import type {
  BatchRunAggregationSummary,
  BatchSuiteRunPayload,
  LLMPlaygroundRunPayload,
  MultiTurnSimulationPayload,
  TestCase,
  TestCaseCreatePayload,
  TestCaseUpdatePayload,
  TestRun,
  TestSuite,
  TestSuiteCreatePayload,
  TestSuiteUpdatePayload,
} from '../types/evaluation';

// ------------------------------------------------------------- Test Suites API

export async function listTestSuites(params?: {
  status?: string;
}): Promise<TestSuite[]> {
  const qs = new URLSearchParams();
  if (params?.status && params.status !== 'all') qs.set('status', params.status);
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  const res = await apiClient.get<TestSuite[]>(
    `/api/v1/testing/suites${suffix}`
  );
  return Array.isArray(res) ? res : [];
}

export async function getTestSuite(suiteId: string): Promise<TestSuite> {
  return apiClient.get<TestSuite>(
    `/api/v1/testing/suites/${encodeURIComponent(suiteId)}`
  );
}

export async function createTestSuite(
  payload: TestSuiteCreatePayload
): Promise<TestSuite> {
  return apiClient.post<TestSuite>('/api/v1/testing/suites', payload);
}

export async function updateTestSuite(
  suiteId: string,
  payload: TestSuiteUpdatePayload
): Promise<TestSuite> {
  return apiClient.patch<TestSuite>(
    `/api/v1/testing/suites/${encodeURIComponent(suiteId)}`,
    payload
  );
}

export async function archiveTestSuite(suiteId: string): Promise<TestSuite> {
  return apiClient.post<TestSuite>(
    `/api/v1/testing/suites/${encodeURIComponent(suiteId)}/archive`,
    {}
  );
}

// -------------------------------------------------------------- Test Cases API

export async function listTestCases(params?: {
  suite_id?: string;
  agent_id?: string;
  enabled_only?: boolean;
}): Promise<TestCase[]> {
  const qs = new URLSearchParams();
  if (params?.suite_id) qs.set('suite_id', params.suite_id);
  if (params?.agent_id) qs.set('agent_id', params.agent_id);
  if (params?.enabled_only) qs.set('enabled_only', 'true');
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  const res = await apiClient.get<TestCase[]>(`/api/v1/testing/cases${suffix}`);
  return Array.isArray(res) ? res : [];
}

export async function getTestCase(caseId: string): Promise<TestCase> {
  return apiClient.get<TestCase>(
    `/api/v1/testing/cases/${encodeURIComponent(caseId)}`
  );
}

export async function createTestCase(
  payload: TestCaseCreatePayload
): Promise<TestCase> {
  return apiClient.post<TestCase>('/api/v1/testing/cases', payload);
}

export async function updateTestCase(
  caseId: string,
  payload: TestCaseUpdatePayload
): Promise<TestCase> {
  return apiClient.patch<TestCase>(
    `/api/v1/testing/cases/${encodeURIComponent(caseId)}`,
    payload
  );
}

export async function deleteTestCase(
  caseId: string
): Promise<{ deleted: boolean; id: string }> {
  return apiClient.delete<{ deleted: boolean; id: string }>(
    `/api/v1/testing/cases/${encodeURIComponent(caseId)}`
  );
}

// ----------------------------------------------------------- Execution & Runs

export async function runSingleTestCase(
  caseId: string,
  options?: {
    agent_version_override?: number;
    dynamic_variables_override?: Record<string, unknown>;
    allow_mock_fallback?: boolean;
  }
): Promise<TestRun> {
  return apiClient.post<TestRun>(
    `/api/v1/testing/cases/${encodeURIComponent(caseId)}/run`,
    {
      agent_version_override: options?.agent_version_override,
      dynamic_variables_override: options?.dynamic_variables_override || {},
      allow_mock_fallback: options?.allow_mock_fallback ?? true,
    }
  );
}

export async function runBatchTestSuite(
  suiteId: string,
  payload?: BatchSuiteRunPayload
): Promise<BatchRunAggregationSummary> {
  return apiClient.post<BatchRunAggregationSummary>(
    `/api/v1/testing/suites/${encodeURIComponent(suiteId)}/run-batch`,
    payload || {}
  );
}

export const runTestSuiteBatch = runBatchTestSuite;

export async function runLLMPlayground(
  payload: LLMPlaygroundRunPayload
): Promise<TestRun> {
  return apiClient.post<TestRun>('/api/v1/testing/playground/run', payload);
}

export async function runMultiTurnSimulation(
  payload: MultiTurnSimulationPayload
): Promise<TestRun> {
  return apiClient.post<TestRun>('/api/v1/testing/simulations/run', payload);
}

export async function listTestRuns(params?: {
  suite_id?: string;
  test_case_id?: string;
  batch_id?: string;
  agent_id?: string;
  agent_version_number?: number;
  mode?: string;
  status?: string;
  limit?: number;
}): Promise<TestRun[]> {
  const qs = new URLSearchParams();
  if (params?.suite_id) qs.set('suite_id', params.suite_id);
  if (params?.test_case_id) qs.set('test_case_id', params.test_case_id);
  if (params?.batch_id) qs.set('batch_id', params.batch_id);
  if (params?.agent_id) qs.set('agent_id', params.agent_id);
  if (params?.agent_version_number !== undefined) {
    qs.set('agent_version_number', String(params.agent_version_number));
  }
  if (params?.mode) qs.set('mode', params.mode);
  if (params?.status && params.status !== 'all') qs.set('status', params.status);
  if (params?.limit !== undefined) qs.set('limit', String(params.limit));
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  const res = await apiClient.get<TestRun[]>(`/api/v1/testing/runs${suffix}`);
  return Array.isArray(res) ? res : [];
}

export async function getTestRun(runId: string): Promise<TestRun> {
  return apiClient.get<TestRun>(
    `/api/v1/testing/runs/${encodeURIComponent(runId)}`
  );
}

export async function cancelTestRun(runId: string): Promise<TestRun> {
  return apiClient.post<TestRun>(
    `/api/v1/testing/runs/${encodeURIComponent(runId)}/cancel`,
    {}
  );
}

export const simulationsApi = {
  listTestSuites,
  getTestSuite,
  createTestSuite,
  updateTestSuite,
  archiveTestSuite,
  listTestCases,
  getTestCase,
  createTestCase,
  updateTestCase,
  deleteTestCase,
  runSingleTestCase,
  runBatchTestSuite,
  runLLMPlayground,
  runMultiTurnSimulation,
  listTestRuns,
  getTestRun,
  cancelTestRun,
};
