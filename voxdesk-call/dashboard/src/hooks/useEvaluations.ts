import { useCallback, useEffect, useState } from 'react';
import {
  compareAgentVersionsQA,
  createEvaluationRule,
  deleteEvaluationRule,
  getAgentQASummary,
  getRunQAScorecard,
  listEvaluationRules,
  listRunEvaluationResults,
  rerunEvaluationOnly,
  updateEvaluationRule,
} from '../api/evaluations';
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

export function useEvaluations(initialFilters?: {
  suite_id?: string;
  test_case_id?: string;
}) {
  const [rules, setRules] = useState<EvaluationRule[]>([]);
  const [selectedRunResults, setSelectedRunResults] = useState<EvaluationResult[]>([]);
  const [scorecardDetail, setScorecardDetail] = useState<RunQAScorecardDetail | null>(null);
  const [agentSummary, setAgentSummary] = useState<AgentVersionQASummary | null>(null);
  const [versionComparison, setVersionComparison] =
    useState<AgentVersionQAComparison | null>(null);
  const [loading, setLoading] = useState(false);
  const [evaluating, setEvaluating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchRules = useCallback(
    async (params?: {
      suite_id?: string;
      test_case_id?: string;
      enabled_only?: boolean;
    }) => {
      setLoading(true);
      setError(null);
      try {
        const items = await listEvaluationRules(params ?? initialFilters);
        setRules(items);
        return items;
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Failed to load evaluation rules';
        setError(msg);
        return [];
      } finally {
        setLoading(false);
      }
    },
    [initialFilters?.suite_id, initialFilters?.test_case_id]
  );

  useEffect(() => {
    void fetchRules();
  }, [fetchRules]);

  const addRule = useCallback(async (payload: EvaluationRuleCreatePayload) => {
    setError(null);
    const created = await createEvaluationRule(payload);
    setRules((prev) => [...prev, created]);
    return created;
  }, []);

  const editRule = useCallback(
    async (ruleId: string, payload: EvaluationRuleUpdatePayload) => {
      setError(null);
      const updated = await updateEvaluationRule(ruleId, payload);
      setRules((prev) => prev.map((r) => (r.id === ruleId ? updated : r)));
      return updated;
    },
    []
  );

  const removeRule = useCallback(async (ruleId: string) => {
    setError(null);
    await deleteEvaluationRule(ruleId);
    setRules((prev) => prev.filter((r) => r.id !== ruleId));
  }, []);

  const loadRunResults = useCallback(async (runId: string) => {
    setLoading(true);
    setError(null);
    try {
      const results = await listRunEvaluationResults(runId);
      setSelectedRunResults(results);
      return results;
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to load evaluation results';
      setError(msg);
      return [];
    } finally {
      setLoading(false);
    }
  }, []);

  const loadRunScorecard = useCallback(async (runId: string) => {
    setLoading(true);
    setError(null);
    try {
      const detail = await getRunQAScorecard(runId);
      setScorecardDetail(detail);
      setSelectedRunResults(detail.results || []);
      return detail;
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to load QA scorecard';
      setError(msg);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  const rerunEvaluation = useCallback(
    async (runId: string, allowMockJudge = true): Promise<TestRun | null> => {
      setEvaluating(true);
      setError(null);
      try {
        const updatedRun = await rerunEvaluationOnly(runId, {
          allow_mock_judge: allowMockJudge,
        });
        setSelectedRunResults(updatedRun.evaluation_results || []);
        if (scorecardDetail && scorecardDetail.test_run_id === runId) {
          await loadRunScorecard(runId);
        }
        return updatedRun;
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Failed to rerun evaluation';
        setError(msg);
        return null;
      } finally {
        setEvaluating(false);
      }
    },
    [loadRunScorecard, scorecardDetail]
  );

  const loadAgentSummary = useCallback(
    async (agentId: string, versionNumber?: number) => {
      setLoading(true);
      setError(null);
      try {
        const summary = await getAgentQASummary(agentId, {
          agent_version_number: versionNumber,
        });
        setAgentSummary(summary);
        return summary;
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Failed to load agent QA summary';
        setError(msg);
        return null;
      } finally {
        setLoading(false);
      }
    },
    []
  );

  const compareVersions = useCallback(
    async (agentId: string, versionA: number, versionB: number) => {
      setLoading(true);
      setError(null);
      try {
        const comp = await compareAgentVersionsQA(agentId, versionA, versionB);
        setVersionComparison(comp);
        return comp;
      } catch (err) {
        const msg =
          err instanceof Error ? err.message : 'Failed to compare agent versions';
        setError(msg);
        return null;
      } finally {
        setLoading(false);
      }
    },
    []
  );

  return {
    rules,
    selectedRunResults,
    scorecardDetail,
    agentSummary,
    versionComparison,
    loading,
    evaluating,
    error,
    fetchRules,
    addRule,
    editRule,
    removeRule,
    loadRunResults,
    loadRunScorecard,
    rerunEvaluation,
    loadAgentSummary,
    compareVersions,
  };
}
