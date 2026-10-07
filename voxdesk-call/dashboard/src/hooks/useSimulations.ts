import { useCallback, useEffect, useState } from 'react';
import {
  archiveTestSuite,
  cancelTestRun,
  createTestCase,
  createTestSuite,
  deleteTestCase,
  getTestRun,
  listTestCases,
  listTestRuns,
  listTestSuites,
  runBatchTestSuite,
  runLLMPlayground,
  runMultiTurnSimulation,
  runSingleTestCase,
  updateTestCase,
  updateTestSuite,
} from '../api/simulations';
import { rerunEvaluationOnly } from '../api/evaluations';
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

export function useSimulations() {
  const [suites, setSuites] = useState<TestSuite[]>([]);
  const [selectedSuite, setSelectedSuite] = useState<TestSuite | null>(null);
  const [cases, setCases] = useState<TestCase[]>([]);
  const [runs, setRuns] = useState<TestRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<TestRun | null>(null);
  const [lastBatchSummary, setLastBatchSummary] =
    useState<BatchRunAggregationSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadSuites = useCallback(async (status?: string) => {
    setLoading(true);
    setError(null);
    try {
      const items = await listTestSuites({ status });
      setSuites(items);
      if (items.length > 0) {
        setSelectedSuite((prev) => prev ?? items[0]);
      }
      return items;
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to load test suites';
      setError(msg);
      return [];
    } finally {
      setLoading(false);
    }
  }, []);

  const loadCases = useCallback(
    async (params?: { suite_id?: string; agent_id?: string }) => {
      setLoading(true);
      setError(null);
      try {
        const items = await listTestCases(params);
        setCases(items);
        return items;
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Failed to load test cases';
        setError(msg);
        return [];
      } finally {
        setLoading(false);
      }
    },
    []
  );

  const loadRuns = useCallback(
    async (params?: {
      suite_id?: string;
      test_case_id?: string;
      batch_id?: string;
      agent_id?: string;
      agent_version_number?: number;
      mode?: string;
      status?: string;
      limit?: number;
    }) => {
      setLoading(true);
      setError(null);
      try {
        const items = await listTestRuns(params);
        setRuns(items);
        if (items.length > 0) {
          setSelectedRun((prev) => prev ?? items[0]);
        }
        return items;
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Failed to load test runs';
        setError(msg);
        return [];
      } finally {
        setLoading(false);
      }
    },
    []
  );

  useEffect(() => {
    void loadSuites();
    void loadRuns();
  }, [loadSuites, loadRuns]);

  useEffect(() => {
    if (selectedSuite) {
      void loadCases({ suite_id: selectedSuite.id });
    }
  }, [selectedSuite, loadCases]);

  const createSuite = useCallback(async (payload: TestSuiteCreatePayload) => {
    setError(null);
    const created = await createTestSuite(payload);
    setSuites((prev) => [created, ...prev]);
    setSelectedSuite(created);
    return created;
  }, []);

  const editSuite = useCallback(
    async (suiteId: string, payload: TestSuiteUpdatePayload) => {
      setError(null);
      const updated = await updateTestSuite(suiteId, payload);
      setSuites((prev) => prev.map((s) => (s.id === suiteId ? updated : s)));
      if (selectedSuite?.id === suiteId) {
        setSelectedSuite(updated);
      }
      return updated;
    },
    [selectedSuite?.id]
  );

  const archiveSuite = useCallback(async (suiteId: string) => {
    setError(null);
    const updated = await archiveTestSuite(suiteId);
    setSuites((prev) => prev.map((s) => (s.id === suiteId ? updated : s)));
    return updated;
  }, []);

  const createCase = useCallback(async (payload: TestCaseCreatePayload) => {
    setError(null);
    const created = await createTestCase(payload);
    setCases((prev) => [...prev, created]);
    return created;
  }, []);

  const editCase = useCallback(
    async (caseId: string, payload: TestCaseUpdatePayload) => {
      setError(null);
      const updated = await updateTestCase(caseId, payload);
      setCases((prev) => prev.map((c) => (c.id === caseId ? updated : c)));
      return updated;
    },
    []
  );

  const removeCase = useCallback(async (caseId: string) => {
    setError(null);
    await deleteTestCase(caseId);
    setCases((prev) => prev.filter((c) => c.id !== caseId));
  }, []);

  const executeCase = useCallback(
    async (
      caseId: string,
      options?: {
        agent_version_override?: number;
        dynamic_variables_override?: Record<string, unknown>;
        allow_mock_fallback?: boolean;
      }
    ) => {
      setRunning(true);
      setError(null);
      try {
        const run = await runSingleTestCase(caseId, options);
        setRuns((prev) => [run, ...prev]);
        setSelectedRun(run);
        return run;
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Test case execution failed';
        setError(msg);
        return null;
      } finally {
        setRunning(false);
      }
    },
    []
  );

  const executeBatchSuite = useCallback(
    async (suiteId: string, payload?: BatchSuiteRunPayload) => {
      setRunning(true);
      setError(null);
      try {
        const summary = await runBatchTestSuite(suiteId, payload);
        setLastBatchSummary(summary);
        if (summary.runs?.length) {
          setRuns((prev) => [...summary.runs, ...prev]);
          setSelectedRun(summary.runs[0]);
        }
        return summary;
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Batch suite execution failed';
        setError(msg);
        return null;
      } finally {
        setRunning(false);
      }
    },
    []
  );

  const executePlayground = useCallback(
    async (payload: LLMPlaygroundRunPayload) => {
      setRunning(true);
      setError(null);
      try {
        const run = await runLLMPlayground(payload);
        setRuns((prev) => [run, ...prev]);
        setSelectedRun(run);
        return run;
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Playground run failed';
        setError(msg);
        return null;
      } finally {
        setRunning(false);
      }
    },
    []
  );

  const executeSimulation = useCallback(
    async (payload: MultiTurnSimulationPayload) => {
      setRunning(true);
      setError(null);
      try {
        const run = await runMultiTurnSimulation(payload);
        setRuns((prev) => [run, ...prev]);
        setSelectedRun(run);
        return run;
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Simulation run failed';
        setError(msg);
        return null;
      } finally {
        setRunning(false);
      }
    },
    []
  );

  const cancelRun = useCallback(async (runId: string) => {
    setError(null);
    const updated = await cancelTestRun(runId);
    setRuns((prev) => prev.map((r) => (r.id === runId ? updated : r)));
    setSelectedRun((prev) => (prev?.id === runId ? updated : prev));
    return updated;
  }, []);

  const refreshRun = useCallback(async (runId: string) => {
    const fresh = await getTestRun(runId);
    setRuns((prev) => prev.map((r) => (r.id === runId ? fresh : r)));
    setSelectedRun(fresh);
    return fresh;
  }, []);

  const reevaluateRunOnly = useCallback(async (runId: string) => {
    setRunning(true);
    setError(null);
    try {
      const updated = await rerunEvaluationOnly(runId);
      setRuns((prev) => prev.map((r) => (r.id === runId ? updated : r)));
      setSelectedRun(updated);
      return updated;
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Re-evaluation failed';
      setError(msg);
      return null;
    } finally {
      setRunning(false);
    }
  }, []);

  return {
    suites,
    selectedSuite,
    setSelectedSuite,
    cases,
    runs,
    selectedRun,
    setSelectedRun,
    lastBatchSummary,
    loading,
    running,
    error,
    loadSuites,
    loadCases,
    loadRuns,
    createSuite,
    editSuite,
    archiveSuite,
    createCase,
    editCase,
    removeCase,
    executeCase,
    executeBatchSuite,
    executePlayground,
    executeSimulation,
    cancelRun,
    refreshRun,
    reevaluateRunOnly,
  };
}
