/**
 * dashboard/src/components/testing/BatchTestTable.tsx
 * Renders durable batch test cases and runs with status, score, duration, and evidence selection.
 */

import React from 'react';
import type { TestRun } from '../../api/types/test-run';
import { TestRunStatus } from './TestRunStatus';

export interface BatchTestTableProps {
  runs: TestRun[];
  selectedRunId?: string | null;
  onSelectRun?: (run: TestRun) => void;
}

export function BatchTestTable({
  runs,
  selectedRunId,
  onSelectRun,
}: BatchTestTableProps) {
  if (runs.length === 0) {
    return (
      <div
        data-testid="batch-test-table-empty"
        style={{
          padding: 18,
          borderRadius: 12,
          background: '#0F1623',
          border: '1px solid #1E2D45',
          color: '#64748B',
          fontSize: 13,
          textAlign: 'center',
        }}
      >
        No batch test runs recorded yet.
      </div>
    );
  }

  return (
    <div
      data-testid="batch-test-table"
      style={{
        borderRadius: 12,
        background: '#0F1623',
        border: '1px solid #1E2D45',
        overflowX: 'auto',
      }}
    >
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
        <thead>
          <tr
            style={{
              textAlign: 'left',
              color: '#94A3B8',
              borderBottom: '1px solid #1E2D45',
            }}
          >
            <th style={{ padding: '10px 12px' }}>Run ID</th>
            <th style={{ padding: '10px 12px' }}>Agent Version</th>
            <th style={{ padding: '10px 12px' }}>Mode</th>
            <th style={{ padding: '10px 12px' }}>Status</th>
            <th style={{ padding: '10px 12px' }}>QA Score</th>
            <th style={{ padding: '10px 12px' }}>Turns</th>
            <th style={{ padding: '10px 12px' }}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => {
            const isSelected = selectedRunId === run.id;
            const score = run.scorecard_summary?.overall_score;
            return (
              <tr
                key={run.id}
                data-testid={`batch-run-row-${run.id}`}
                style={{
                  borderBottom: '1px solid #141D2E',
                  background: isSelected ? 'rgba(37,99,235,0.12)' : 'transparent',
                  color: '#E2E8F0',
                }}
              >
                <td style={{ padding: '10px 12px', fontFamily: 'monospace' }}>
                  {run.id.slice(0, 8)}
                </td>
                <td style={{ padding: '10px 12px' }}>v{run.agent_version_number}</td>
                <td style={{ padding: '10px 12px' }}>{run.mode}</td>
                <td style={{ padding: '10px 12px' }}>
                  <TestRunStatus
                    status={run.status}
                    isMockProvider={run.is_mock_provider}
                    errorCode={run.error_code}
                  />
                </td>
                <td style={{ padding: '10px 12px', fontWeight: 700 }}>
                  {typeof score === 'number' ? `${Math.round(score)}%` : 'N/A'}
                </td>
                <td style={{ padding: '10px 12px' }}>
                  {run.transcript_snapshot?.length || 0}
                </td>
                <td style={{ padding: '10px 12px' }}>
                  {onSelectRun && (
                    <button
                      type="button"
                      onClick={() => onSelectRun(run)}
                      style={{
                        padding: '4px 10px',
                        borderRadius: 6,
                        border: '1px solid #334155',
                        background: '#1E293B',
                        color: '#E2E8F0',
                        fontSize: 11.5,
                        cursor: 'pointer',
                      }}
                    >
                      Inspect Evidence
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default BatchTestTable;
