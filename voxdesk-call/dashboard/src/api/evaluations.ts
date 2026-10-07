import { apiClient } from './client';
import type {
  AgentVersionQAComparison,
  AgentVersionQASummary,
  EvaluationResult,
  EvaluationRule,
  EvaluationRuleCreatePayload,
  EvaluationRuleUpdatePayload,
  RunQAScorecardDetail,
  TestRun,
} from '../types/evaluation';

export async function listEvaluationRules(params?: {
  suite_id?: string;
  test_case_id?: string;
  enabled_only?: boolean;
}): Promise<EvaluationRule[]> {
  const qs = new URLSearchParams();
  if (params?.suite_id) qs.set('suite_id', params.suite_id);
  if (params?.test_case_id) qs.set('test_case_id', params.test_case_id);
  if (params?.enabled_only) qs.set('enabled_only', 'true');
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  const res = await apiClient.get<EvaluationRule[]>(
    `/api/v1/evaluations/rules${suffix}`
  );
  return Array.isArray(res) ? res : [];
}

export async function getEvaluationRule(ruleId: string): Promise<EvaluationRule> {
  return apiClient.get<EvaluationRule>(
    `/api/v1/evaluations/rules/${encodeURIComponent(ruleId)}`
  );
}

export async function createEvaluationRule(
  payload: EvaluationRuleCreatePayload
): Promise<EvaluationRule> {
  return apiClient.post<EvaluationRule>('/api/v1/evaluations/rules', payload);
}

export async function updateEvaluationRule(
  ruleId: string,
  payload: EvaluationRuleUpdatePayload
): Promise<EvaluationRule> {
  return apiClient.patch<EvaluationRule>(
    `/api/v1/evaluations/rules/${encodeURIComponent(ruleId)}`,
    payload
  );
}

export async function deleteEvaluationRule(
  ruleId: string
): Promise<{ deleted: boolean; id: string }> {
  return apiClient.delete<{ deleted: boolean; id: string }>(
    `/api/v1/evaluations/rules/${encodeURIComponent(ruleId)}`
  );
}

export async function listRunEvaluationResults(
  runId: string
): Promise<EvaluationResult[]> {
  const res = await apiClient.get<EvaluationResult[]>(
    `/api/v1/evaluations/runs/${encodeURIComponent(runId)}/results`
  );
  return Array.isArray(res) ? res : [];
}

export async function rerunEvaluationOnly(
  runId: string,
  options?: { allow_mock_judge?: boolean }
): Promise<TestRun> {
  return apiClient.post<TestRun>(
    `/api/v1/evaluations/runs/${encodeURIComponent(runId)}/rerun`,
    { allow_mock_judge: options?.allow_mock_judge ?? true }
  );
}

export const rerunTestRunEvaluation = rerunEvaluationOnly;

export async function getRunQAScorecard(
  runId: string
): Promise<RunQAScorecardDetail> {
  return apiClient.get<RunQAScorecardDetail>(
    `/api/qa/test-runs/${encodeURIComponent(runId)}/scorecard`
  );
}

export async function getAgentQASummary(
  agentId: string,
  params?: { agent_version_number?: number; limit?: number }
): Promise<AgentVersionQASummary> {
  const qs = new URLSearchParams();
  if (params?.agent_version_number !== undefined) {
    qs.set('agent_version_number', String(params.agent_version_number));
  }
  if (params?.limit !== undefined) {
    qs.set('limit', String(params.limit));
  }
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  return apiClient.get<AgentVersionQASummary>(
    `/api/qa/agents/${encodeURIComponent(agentId)}/summary${suffix}`
  );
}

export async function compareAgentVersionsQA(
  agentId: string,
  versionA: number,
  versionB: number
): Promise<AgentVersionQAComparison> {
  const qs = new URLSearchParams({
    version_a: String(versionA),
    version_b: String(versionB),
  });
  return apiClient.get<AgentVersionQAComparison>(
    `/api/qa/agents/${encodeURIComponent(agentId)}/compare-versions?${qs.toString()}`
  );
}

export const evaluationsApi = {
  listEvaluationRules,
  getEvaluationRule,
  createEvaluationRule,
  updateEvaluationRule,
  deleteEvaluationRule,
  listRunEvaluationResults,
  rerunEvaluationOnly,
  getRunQAScorecard,
  getAgentQASummary,
  compareAgentVersionsQA,
};
