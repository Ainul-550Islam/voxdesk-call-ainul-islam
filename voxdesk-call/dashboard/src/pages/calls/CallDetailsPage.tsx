/**
 * dashboard/src/pages/calls/CallDetailsPage.tsx
 * Reconciles test-call detail UI with persisted TestRun and EvaluationResult evidence.
 */

import React, { useEffect, useState } from 'react';
import { getTestRun } from '../../api/simulations';
import type { TestRun } from '../../api/types/test-run';
import { EvaluationScorecard } from '../../components/testing/EvaluationScorecard';
import { TestRunStatus } from '../../components/testing/TestRunStatus';
import { TranscriptEvidence } from '../../components/testing/TranscriptEvidence';

export interface CallDetailsPageProps {
  runId?: string;
}

export function CallDetailsPage({ runId }: CallDetailsPageProps) {
  const [run, setRun] = useState<TestRun | null>(null);
  const [loading, setLoading] = useState<boolean>(Boolean(runId));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) return;
    let active = true;
    setLoading(true);
    getTestRun(runId)
      .then((res) => {
        if (active) setRun(res);
      })
      .catch((err) => {
        if (active) {
          setError(err instanceof Error ? err.message : 'Failed to load call test run');
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [runId]);

  return (
    <div
      data-testid="call-details-page"
      style={{
        minHeight: '100vh',
        background: '#06090F',
        color: '#F1F5F9',
        padding: 28,
      }}
    >
      <div
        style={{
          maxWidth: 1024,
          margin: '0 auto',
          display: 'flex',
          flexDirection: 'column',
          gap: 18,
        }}
      >
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>
          Call & Test Run Evidence Detail
        </h1>

        {loading && <div style={{ color: '#94A3B8' }}>Loading run details...</div>}
        {error && <div style={{ color: '#F87171' }}>{error}</div>}

        {run && (
          <>
            <div
              style={{
                padding: 16,
                borderRadius: 12,
                background: '#0F1623',
                border: '1px solid #1E2D45',
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <div>
                <div style={{ fontWeight: 700, fontSize: 15 }}>
                  Run {run.id} · Agent v{run.agent_version_number}
                </div>
                <div style={{ fontSize: 12, color: '#94A3B8', marginTop: 4 }}>
                  Mode: {run.mode} · Provider: {run.provider} · Config Hash:{' '}
                  <code>{run.agent_config_hash}</code>
                </div>
              </div>
              <TestRunStatus
                status={run.status}
                isMockProvider={run.is_mock_provider}
                errorCode={run.error_code}
              />
            </div>

            <EvaluationScorecard
              scorecard={run.scorecard_summary}
              results={run.evaluation_results || []}
            />

            <TranscriptEvidence
              transcript={run.transcript_snapshot || []}
              events={run.events_snapshot || []}
            />
          </>
        )}
      </div>
    </div>
  );
}

export default CallDetailsPage;
