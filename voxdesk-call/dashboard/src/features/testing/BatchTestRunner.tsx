import React, { useState } from 'react';
import type {
  BatchRunAggregationSummary,
  TestCase,
  TestRun,
  TestSuite,
} from '../../types/evaluation';

export interface BatchTestRunnerProps {
  suite: TestSuite | null;
  cases: TestCase[];
  lastBatchSummary: BatchRunAggregationSummary | null;
  running?: boolean;
  onRunBatch: (
    suiteId: string,
    options?: { agent_version_override?: number }
  ) => Promise<BatchRunAggregationSummary | null>;
  onRunSingleCase: (
    caseId: string,
    options?: { agent_version_override?: number }
  ) => Promise<TestRun | null>;
  onSelectRun?: (run: TestRun) => void;
  onDeleteCase?: (caseId: string) => Promise<unknown>;
}

function statusBadgeStyle(status?: string | null): React.CSSProperties {
  const s = (status || '').toLowerCase();
  if (s === 'passed') {
    return {
      background: 'rgba(16, 185, 129, 0.16)',
      color: '#34d399',
      border: '1px solid rgba(16, 185, 129, 0.35)',
    };
  }
  if (s === 'failed') {
    return {
      background: 'rgba(239, 68, 68, 0.16)',
      color: '#f87171',
      border: '1px solid rgba(239, 68, 68, 0.35)',
    };
  }
  if (s === 'error') {
    return {
      background: 'rgba(245, 158, 11, 0.18)',
      color: '#fbbf24',
      border: '1px solid rgba(245, 158, 11, 0.4)',
    };
  }
  return {
    background: 'rgba(148, 163, 184, 0.16)',
    color: '#cbd5e1',
    border: '1px solid rgba(148, 163, 184, 0.3)',
  };
}

export const BatchTestRunner: React.FC<BatchTestRunnerProps> = ({
  suite,
  cases,
  lastBatchSummary,
  running = false,
  onRunBatch,
  onRunSingleCase,
  onSelectRun,
  onDeleteCase,
}) => {
  const [versionOverride, setVersionOverride] = useState<string>('');

  if (!suite) {
    return (
      <div
        data-testid="batch-runner-empty"
        style={{
          padding: 20,
          borderRadius: 12,
          background: 'rgba(15, 23, 42, 0.75)',
          border: '1px solid rgba(148, 163, 184, 0.2)',
          color: '#94a3b8',
          fontSize: 13,
        }}
      >
        Select or create a Test Suite to manage version-pinned test cases and execute batch runs.
      </div>
    );
  }

  const parsedOverride = versionOverride.trim()
    ? Number(versionOverride.trim())
    : undefined;

  return (
    <div
      data-testid="batch-test-runner"
      style={{
        background: 'rgba(15, 23, 42, 0.75)',
        border: '1px solid rgba(148, 163, 184, 0.2)',
        borderRadius: 12,
        padding: 20,
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: 12,
          flexWrap: 'wrap',
          marginBottom: 16,
        }}
      >
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: '#f8fafc' }}>
              {suite.name}
            </h3>
            <span
              style={{
                padding: '2px 8px',
                borderRadius: 999,
                fontSize: 11,
                fontWeight: 600,
                background: 'rgba(59, 130, 246, 0.16)',
                color: '#93c5fd',
              }}
            >
              Policy: {suite.pass_policy} ({suite.min_pass_score}%)
            </span>
          </div>
          {suite.description && (
            <p style={{ margin: '4px 0 0', fontSize: 12, color: '#94a3b8' }}>
              {suite.description}
            </p>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <input
            type="number"
            min="1"
            aria-label="Batch Version Override"
            placeholder="Version override (optional)"
            value={versionOverride}
            onChange={(e) => setVersionOverride(e.target.value)}
            style={{
              width: 175,
              padding: '7px 10px',
              borderRadius: 8,
              border: '1px solid rgba(148, 163, 184, 0.3)',
              background: '#0f172a',
              color: '#f8fafc',
              fontSize: 12,
            }}
          />
          <button
            type="button"
            data-testid="run-batch-suite-btn"
            disabled={running || cases.length === 0}
            onClick={() =>
              void onRunBatch(suite.id, {
                agent_version_override: parsedOverride,
              })
            }
            style={{
              padding: '8px 16px',
              borderRadius: 8,
              border: 'none',
              background:
                running || cases.length === 0 ? '#475569' : '#10b981',
              color: '#ffffff',
              fontWeight: 700,
              fontSize: 13,
              cursor:
                running || cases.length === 0 ? 'not-allowed' : 'pointer',
            }}
          >
            {running ? 'Running Batch...' : `Run Batch Suite (${cases.length})`}
          </button>
        </div>
      </div>

      {lastBatchSummary && lastBatchSummary.suite_id === suite.id && (
        <div
          data-testid="batch-summary-banner"
          style={{
            marginBottom: 16,
            padding: 14,
            borderRadius: 10,
            background: 'rgba(30, 41, 59, 0.85)',
            border: '1px solid rgba(148, 163, 184, 0.25)',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            flexWrap: 'wrap',
            gap: 12,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span
              data-testid="batch-overall-status"
              style={{
                padding: '4px 10px',
                borderRadius: 999,
                fontSize: 12,
                fontWeight: 700,
                textTransform: 'uppercase',
                ...statusBadgeStyle(lastBatchSummary.overall_status),
              }}
            >
              BATCH {lastBatchSummary.overall_status}
            </span>
            <span style={{ fontSize: 13, color: '#e2e8f0' }}>
              Batch ID: <code>{lastBatchSummary.batch_id}</code>
            </span>
          </div>

          <div style={{ display: 'flex', gap: 14, fontSize: 12, color: '#cbd5e1' }}>
            <span>Total: {lastBatchSummary.total_cases}</span>
            <span style={{ color: '#34d399' }}>
              Passed: {lastBatchSummary.passed_count}
            </span>
            <span style={{ color: '#f87171' }}>
              Failed: {lastBatchSummary.failed_count}
            </span>
            <span style={{ color: '#fbbf24' }}>
              Errors: {lastBatchSummary.error_count}
            </span>
            <span>
              Avg Score:{' '}
              <strong>
                {lastBatchSummary.average_score !== null
                  ? `${lastBatchSummary.average_score}%`
                  : 'NO_ASSERTIONS'}
              </strong>
            </span>
          </div>
        </div>
      )}

      {cases.length === 0 ? (
        <div style={{ fontSize: 13, color: '#94a3b8', padding: '12px 0' }}>
          No test cases in this suite yet. Add a version-pinned test case below.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {cases.map((tc) => {
            const matchingBatchRun = lastBatchSummary?.runs?.find(
              (r) => r.test_case_id === tc.id
            );
            return (
              <div
                key={tc.id}
                data-testid={`test-case-row-${tc.id}`}
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  padding: '10px 14px',
                  borderRadius: 8,
                  background: 'rgba(15, 23, 42, 0.9)',
                  border: '1px solid rgba(148, 163, 184, 0.16)',
                  gap: 12,
                  flexWrap: 'wrap',
                }}
              >
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <strong style={{ fontSize: 13, color: '#f8fafc' }}>
                      {tc.name}
                    </strong>
                    <span
                      data-testid={`case-pinned-version-${tc.id}`}
                      style={{
                        padding: '2px 8px',
                        borderRadius: 6,
                        fontSize: 11,
                        fontWeight: 700,
                        background: 'rgba(139, 92, 246, 0.18)',
                        color: '#c4b5fd',
                        fontFamily: 'monospace',
                      }}
                    >
                      Pinned v{tc.agent_version_number}
                    </span>
                    <span
                      style={{
                        padding: '2px 8px',
                        borderRadius: 6,
                        fontSize: 11,
                        background: 'rgba(59, 130, 246, 0.14)',
                        color: '#93c5fd',
                      }}
                    >
                      {tc.mode} · {tc.agent_kind}
                    </span>
                    {matchingBatchRun && (
                      <span
                        style={{
                          padding: '2px 8px',
                          borderRadius: 6,
                          fontSize: 11,
                          fontWeight: 700,
                          textTransform: 'uppercase',
                          ...statusBadgeStyle(matchingBatchRun.status),
                        }}
                      >
                        {matchingBatchRun.status}
                      </span>
                    )}
                  </div>
                  <div style={{ marginTop: 4, fontSize: 12, color: '#94a3b8' }}>
                    Agent: <code>{tc.agent_id}</code> · Turns:{' '}
                    {tc.input_messages?.length || 0} · Inline Rules:{' '}
                    {tc.expected_rules?.length || 0}
                  </div>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  {matchingBatchRun && onSelectRun && (
                    <button
                      type="button"
                      onClick={() => onSelectRun(matchingBatchRun)}
                      style={{
                        padding: '5px 10px',
                        borderRadius: 6,
                        border: '1px solid rgba(59, 130, 246, 0.35)',
                        background: 'rgba(59, 130, 246, 0.14)',
                        color: '#93c5fd',
                        fontSize: 12,
                        cursor: 'pointer',
                      }}
                    >
                      Inspect Trace
                    </button>
                  )}
                  <button
                    type="button"
                    data-testid={`run-case-btn-${tc.id}`}
                    disabled={running}
                    onClick={() =>
                      void onRunSingleCase(tc.id, {
                        agent_version_override: parsedOverride,
                      })
                    }
                    style={{
                      padding: '5px 12px',
                      borderRadius: 6,
                      border: 'none',
                      background: '#3b82f6',
                      color: '#ffffff',
                      fontSize: 12,
                      fontWeight: 600,
                      cursor: running ? 'not-allowed' : 'pointer',
                    }}
                  >
                    Run Case
                  </button>
                  {onDeleteCase && (
                    <button
                      type="button"
                      onClick={() => void onDeleteCase(tc.id)}
                      style={{
                        padding: '5px 10px',
                        borderRadius: 6,
                        border: '1px solid rgba(239, 68, 68, 0.3)',
                        background: 'rgba(239, 68, 68, 0.12)',
                        color: '#fca5a5',
                        fontSize: 12,
                        cursor: 'pointer',
                      }}
                    >
                      Remove
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default BatchTestRunner;
