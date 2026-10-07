import React from 'react';
import type { EvaluationResult, TestRun } from '../../types/evaluation';

export interface SimulationTraceViewerProps {
  run: TestRun | null;
  evaluating?: boolean;
  onRerunEvaluation?: (runId: string) => Promise<unknown>;
}

export const SimulationTraceViewer: React.FC<SimulationTraceViewerProps> = ({
  run,
  evaluating = false,
  onRerunEvaluation,
}) => {
  if (!run) {
    return (
      <div
        data-testid="trace-viewer-empty"
        style={{
          padding: 24,
          borderRadius: 12,
          background: 'rgba(15, 23, 42, 0.75)',
          border: '1px solid rgba(148, 163, 184, 0.2)',
          color: '#94a3b8',
          fontSize: 13,
        }}
      >
        Select or execute a TestRun to inspect its version-pinned transcript, tool calls,
        latency telemetry, and QA evaluation evidence.
      </div>
    );
  }

  const scorecard = run.scorecard_summary || {
    status: 'NO_ASSERTIONS',
    overall_score: null,
    explanation: 'No evaluation summary recorded.',
  };
  const results: EvaluationResult[] = run.evaluation_results || [];

  const getScorecardTone = (status?: string) => {
    const s = (status || '').toUpperCase();
    if (s === 'PASSED') return { bg: 'rgba(16, 185, 129, 0.16)', fg: '#34d399' };
    if (s === 'FAILED_ASSERTION')
      return { bg: 'rgba(239, 68, 68, 0.16)', fg: '#f87171' };
    if (s === 'EVALUATION_ERROR')
      return { bg: 'rgba(245, 158, 11, 0.2)', fg: '#fbbf24' };
    return { bg: 'rgba(148, 163, 184, 0.18)', fg: '#cbd5e1' };
  };

  const tone = getScorecardTone(scorecard.status);

  return (
    <div
      data-testid="simulation-trace-viewer"
      style={{
        background: 'rgba(15, 23, 42, 0.8)',
        border: '1px solid rgba(148, 163, 184, 0.22)',
        borderRadius: 12,
        padding: 20,
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
      }}
    >
      {/* Header Metadata Bar */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          flexWrap: 'wrap',
          gap: 12,
        }}
      >
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: '#f8fafc' }}>
              Execution Trace · {run.mode.toUpperCase()}
            </h3>
            <span
              data-testid="trace-pinned-version"
              style={{
                padding: '3px 9px',
                borderRadius: 6,
                fontSize: 12,
                fontWeight: 700,
                background: 'rgba(139, 92, 246, 0.2)',
                color: '#c4b5fd',
                fontFamily: 'monospace',
              }}
            >
              Agent {run.agent_id} · v{run.agent_version_number}
            </span>
            <span
              data-testid="trace-provider-badge"
              style={{
                padding: '3px 9px',
                borderRadius: 6,
                fontSize: 11,
                fontWeight: 600,
                background: run.is_mock_provider
                  ? 'rgba(245, 158, 11, 0.16)'
                  : 'rgba(16, 185, 129, 0.16)',
                color: run.is_mock_provider ? '#fbbf24' : '#34d399',
              }}
            >
              {run.is_mock_provider
                ? `SANDBOX MOCK (${run.provider}/${run.model})`
                : `LIVE PROVIDER (${run.provider}/${run.model})`}
            </span>
            <span
              data-testid="trace-run-status"
              style={{
                padding: '3px 9px',
                borderRadius: 6,
                fontSize: 11,
                fontWeight: 700,
                textTransform: 'uppercase',
                background:
                  run.status === 'passed'
                    ? 'rgba(16, 185, 129, 0.18)'
                    : run.status === 'failed'
                    ? 'rgba(239, 68, 68, 0.18)'
                    : 'rgba(245, 158, 11, 0.18)',
                color:
                  run.status === 'passed'
                    ? '#34d399'
                    : run.status === 'failed'
                    ? '#f87171'
                    : '#fbbf24',
              }}
            >
              RUN: {run.status}
            </span>
          </div>
          <div style={{ marginTop: 6, fontSize: 12, color: '#94a3b8' }}>
            Run ID: <code>{run.id}</code> · Config Hash:{' '}
            <code>{run.agent_config_hash || 'n/a'}</code> · Duration:{' '}
            {run.duration_ms ?? 0}ms · Tokens:{' '}
            {run.usage_metadata?.total_tokens ?? 0}
          </div>
        </div>

        {onRerunEvaluation && (
          <button
            type="button"
            data-testid="rerun-evaluation-only-btn"
            disabled={evaluating}
            onClick={() => void onRerunEvaluation(run.id)}
            style={{
              padding: '8px 14px',
              borderRadius: 8,
              border: '1px solid rgba(59, 130, 246, 0.45)',
              background: 'rgba(59, 130, 246, 0.18)',
              color: '#93c5fd',
              fontWeight: 600,
              fontSize: 12,
              cursor: evaluating ? 'not-allowed' : 'pointer',
            }}
          >
            {evaluating
              ? 'Re-evaluating...'
              : 'Re-evaluate Rules Only (No Call Re-run)'}
          </button>
        )}
      </div>

      {/* Honest Runtime Error / Not Run Notice */}
      {(run.error_code || run.error_message) && (
        <div
          role="alert"
          data-testid="trace-error-banner"
          style={{
            padding: 12,
            borderRadius: 8,
            background: 'rgba(239, 68, 68, 0.14)',
            border: '1px solid rgba(239, 68, 68, 0.35)',
            color: '#fca5a5',
            fontSize: 12,
          }}
        >
          <strong>{run.error_code || 'EXECUTION_ERROR'}:</strong>{' '}
          {run.error_message}
        </div>
      )}

      {/* QA Scorecard Banner */}
      <div
        data-testid="trace-scorecard-banner"
        style={{
          padding: 14,
          borderRadius: 10,
          background: tone.bg,
          border: `1px solid ${tone.fg}44`,
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: 12,
        }}
      >
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span
              data-testid="trace-scorecard-status"
              style={{
                fontWeight: 800,
                fontSize: 13,
                color: tone.fg,
              }}
            >
              QA SCORECARD: {scorecard.status}
            </span>
            <span style={{ fontSize: 12, color: '#e2e8f0' }}>
              Formula: <code>{scorecard.formula_version || 'weighted_v1'}</code> ·
              Evaluator: <code>{scorecard.evaluator_version || 'v1.0'}</code>
            </span>
          </div>
          <div style={{ marginTop: 4, fontSize: 12, color: '#e2e8f0' }}>
            {scorecard.explanation}
          </div>
        </div>

        <div
          data-testid="trace-scorecard-score"
          style={{
            fontSize: 18,
            fontWeight: 800,
            color: tone.fg,
          }}
        >
          {scorecard.overall_score !== null &&
          scorecard.overall_score !== undefined
            ? `${scorecard.overall_score}%`
            : 'NO_ASSERTIONS'}
        </div>
      </div>

      {/* Transcript Turns */}
      <div>
        <h4 style={{ margin: '0 0 10px', fontSize: 14, color: '#e2e8f0' }}>
          Conversation Transcript ({run.transcript_snapshot?.length || 0} turns)
        </h4>
        {!run.transcript_snapshot || run.transcript_snapshot.length === 0 ? (
          <div style={{ fontSize: 12, color: '#94a3b8' }}>
            No transcript turns recorded for this run.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {run.transcript_snapshot.map((turn, idx) => {
              const isUser = turn.role === 'user';
              return (
                <div
                  key={idx}
                  data-testid={`transcript-turn-${idx}`}
                  style={{
                    padding: '10px 14px',
                    borderRadius: 8,
                    background: isUser
                      ? 'rgba(30, 41, 59, 0.8)'
                      : 'rgba(15, 23, 42, 0.95)',
                    borderLeft: isUser
                      ? '3px solid #60a5fa'
                      : '3px solid #34d399',
                  }}
                >
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      fontSize: 11,
                      color: '#94a3b8',
                      marginBottom: 4,
                    }}
                  >
                    <span>
                      <strong>{turn.role.toUpperCase()}</strong> · Turn #{idx}
                      {turn.intent ? ` · intent=${turn.intent}` : ''}
                    </span>
                    {turn.latency_ms !== undefined &&
                      turn.latency_ms !== null && (
                        <span>{turn.latency_ms}ms</span>
                      )}
                  </div>
                  <div style={{ fontSize: 13, color: '#f8fafc' }}>
                    {turn.content}
                  </div>
                  {turn.tool_calls && turn.tool_calls.length > 0 && (
                    <div
                      style={{
                        marginTop: 8,
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 4,
                      }}
                    >
                      {turn.tool_calls.map((tc, tIdx) => (
                        <div
                          key={tIdx}
                          data-testid={`turn-${idx}-tool-${tIdx}`}
                          style={{
                            padding: '6px 10px',
                            borderRadius: 6,
                            background: 'rgba(59, 130, 246, 0.12)',
                            border: '1px solid rgba(59, 130, 246, 0.25)',
                            fontSize: 11,
                            color: '#93c5fd',
                            fontFamily: 'monospace',
                          }}
                        >
                          Tool Call: <strong>{tc.name || tc.tool_name}</strong>(
                          {JSON.stringify(tc.arguments || {})}) →{' '}
                          {JSON.stringify(tc.result || {})}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Rule-by-Rule Evaluation Evidence */}
      <div>
        <h4 style={{ margin: '0 0 10px', fontSize: 14, color: '#e2e8f0' }}>
          Assertion Results & Evidence ({results.length})
        </h4>
        {results.length === 0 ? (
          <div
            data-testid="no-eval-results-msg"
            style={{ fontSize: 12, color: '#94a3b8' }}
          >
            Zero enabled evaluation rules were applied to this run (
            <code>NO_ASSERTIONS</code>).
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {results.map((res) => {
              const passed = res.status === 'passed';
              return (
                <div
                  key={res.id}
                  data-testid={`eval-result-item-${res.rule_type}`}
                  style={{
                    padding: '10px 14px',
                    borderRadius: 8,
                    background: 'rgba(15, 23, 42, 0.92)',
                    border: passed
                      ? '1px solid rgba(16, 185, 129, 0.3)'
                      : '1px solid rgba(239, 68, 68, 0.35)',
                  }}
                >
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      gap: 8,
                    }}
                  >
                    <div>
                      <strong style={{ color: '#f8fafc', fontSize: 13 }}>
                        {res.rule_name}
                      </strong>{' '}
                      <code style={{ fontSize: 11, color: '#93c5fd' }}>
                        [{res.rule_type}]
                      </code>
                    </div>
                    <span
                      style={{
                        padding: '2px 8px',
                        borderRadius: 6,
                        fontSize: 11,
                        fontWeight: 700,
                        textTransform: 'uppercase',
                        background: passed
                          ? 'rgba(16, 185, 129, 0.16)'
                          : 'rgba(239, 68, 68, 0.16)',
                        color: passed ? '#34d399' : '#f87171',
                      }}
                    >
                      {res.status} (score: {res.score}, weight: {res.weight})
                    </span>
                  </div>
                  <div style={{ marginTop: 4, fontSize: 12, color: '#cbd5e1' }}>
                    {res.explanation}
                  </div>
                  <div
                    style={{
                      marginTop: 6,
                      fontSize: 11,
                      color: '#94a3b8',
                      fontFamily: 'monospace',
                    }}
                  >
                    Evidence: expected={JSON.stringify(res.evidence?.expected)} ·
                    actual={JSON.stringify(res.evidence?.actual)}
                    {res.evidence?.matched_turn_indices?.length
                      ? ` · matched_turns=${JSON.stringify(
                          res.evidence.matched_turn_indices
                        )}`
                      : ''}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

export default SimulationTraceViewer;
