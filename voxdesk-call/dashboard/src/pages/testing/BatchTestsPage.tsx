/**
 * dashboard/src/pages/testing/BatchTestsPage.tsx
 * Batch suite and case management and run history with real status aggregation
 * and evidence-backed QA scorecards.
 */

import React, { useEffect, useState } from 'react';
import { listTestRuns, listTestSuites, runBatchTestSuite } from '../../api/simulations';
import type {
  BatchRunAggregationSummary,
  TestRun,
  TestSuite,
} from '../../api/types/test-run';
import { BatchTestTable } from '../../components/testing/BatchTestTable';
import { EvaluationScorecard } from '../../components/testing/EvaluationScorecard';
import { TranscriptEvidence } from '../../components/testing/TranscriptEvidence';

export function BatchTestsPage() {
  const [suites, setSuites] = useState<TestSuite[]>([]);
  const [runs, setRuns] = useState<TestRun[]>([]);
  const [selectedSuiteId, setSelectedSuiteId] = useState<string>('');
  const [selectedRun, setSelectedRun] = useState<TestRun | null>(null);
  const [batchSummary, setBatchSummary] = useState<BatchRunAggregationSummary | null>(
    null,
  );
  const [loading, setLoading] = useState<boolean>(true);
  const [runningBatch, setRunningBatch] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    async function load() {
      setLoading(true);
      try {
        const [suiteList, runList] = await Promise.all([
          listTestSuites(),
          listTestRuns({ limit: 50 }),
        ]);
        if (!active) return;
        setSuites(suiteList);
        if (suiteList.length > 0) {
          setSelectedSuiteId(suiteList[0].id);
        }
        setRuns(runList);
        if (runList.length > 0) {
          setSelectedRun(runList[0]);
        }
      } catch (err) {
        if (active) {
          setError(err instanceof Error ? err.message : 'Failed to load batch suites');
        }
      } finally {
        if (active) setLoading(false);
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, []);

  const handleRunBatch = async () => {
    if (!selectedSuiteId) return;
    setRunningBatch(true);
    setError(null);
    try {
      const summary = await runBatchTestSuite(selectedSuiteId, {
        allow_mock_fallback: true,
      });
      setBatchSummary(summary);
      setRuns(summary.runs);
      if (summary.runs.length > 0) {
        setSelectedRun(summary.runs[0]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Batch execution failed');
    } finally {
      setRunningBatch(false);
    }
  };

  return (
    <div
      data-testid="batch-tests-page"
      style={{
        minHeight: '100vh',
        background: '#06090F',
        color: '#F1F5F9',
        padding: 28,
      }}
    >
      <div
        style={{
          maxWidth: 1120,
          margin: '0 auto',
          display: 'flex',
          flexDirection: 'column',
          gap: 20,
        }}
      >
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            flexWrap: 'wrap',
            gap: 12,
          }}
        >
          <div>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>
              Batch Test Suites & Run History
            </h1>
            <p style={{ margin: '4px 0 0', fontSize: 13, color: '#94A3B8' }}>
              Execute version-pinned test suites and inspect per-case QA evidence.
            </p>
          </div>

          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <select
              value={selectedSuiteId}
              onChange={(e) => setSelectedSuiteId(e.target.value)}
              style={{
                padding: '8px 12px',
                borderRadius: 8,
                border: '1px solid #1E2D45',
                background: '#0F1623',
                color: '#F1F5F9',
                fontSize: 13,
              }}
            >
              {suites.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} ({s.case_count} cases)
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => void handleRunBatch()}
              disabled={!selectedSuiteId || runningBatch}
              style={{
                padding: '9px 16px',
                borderRadius: 8,
                border: 'none',
                background: '#2563EB',
                color: '#FFFFFF',
                fontSize: 13,
                fontWeight: 600,
                cursor: runningBatch ? 'wait' : 'pointer',
              }}
            >
              {runningBatch ? 'Running Batch...' : 'Run Selected Suite'}
            </button>
          </div>
        </div>

        {error && (
          <div
            style={{
              padding: 12,
              borderRadius: 8,
              background: 'rgba(239,68,68,0.12)',
              border: '1px solid rgba(239,68,68,0.35)',
              color: '#FCA5A5',
              fontSize: 13,
            }}
          >
            {error}
          </div>
        )}

        {batchSummary && (
          <div
            style={{
              padding: 16,
              borderRadius: 12,
              background: '#0F1623',
              border: '1px solid #1E2D45',
              fontSize: 13,
            }}
          >
            Batch <code>{batchSummary.batch_id.slice(0, 8)}</code> · Overall:{' '}
            <strong>{batchSummary.overall_status.toUpperCase()}</strong> · Passed:{' '}
            {batchSummary.passed_count}/{batchSummary.total_cases} · Avg Score:{' '}
            {batchSummary.average_score !== null
              ? `${Math.round(batchSummary.average_score)}%`
              : 'N/A'}
          </div>
        )}

        {loading ? (
          <div style={{ color: '#94A3B8', fontSize: 13 }}>Loading batch test runs...</div>
        ) : (
          <BatchTestTable
            runs={runs}
            selectedRunId={selectedRun?.id}
            onSelectRun={(r) => setSelectedRun(r)}
          />
        )}

        {selectedRun && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
            <EvaluationScorecard
              scorecard={selectedRun.scorecard_summary}
              results={selectedRun.evaluation_results || []}
            />
            <TranscriptEvidence
              transcript={selectedRun.transcript_snapshot || []}
              events={selectedRun.events_snapshot || []}
            />
          </div>
        )}
      </div>
    </div>
  );
}

export default BatchTestsPage;
