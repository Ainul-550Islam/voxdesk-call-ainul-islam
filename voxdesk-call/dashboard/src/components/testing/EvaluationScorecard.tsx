/**
 * dashboard/src/components/testing/EvaluationScorecard.tsx
 * Evidence-backed QA scorecard displaying per-rule results, score calculation,
 * evaluator metadata, and failure details.
 */

import React from 'react';
import type {
  EvaluationResult,
  QAScorecardSummary,
} from '../../api/types/evaluation';

export interface EvaluationScorecardProps {
  scorecard: QAScorecardSummary;
  results: EvaluationResult[];
  onRerunEvaluation?: () => void;
  rerunning?: boolean;
}

export function EvaluationScorecard({
  scorecard,
  results,
  onRerunEvaluation,
  rerunning = false,
}: EvaluationScorecardProps) {
  const isPass = scorecard.status === 'PASSED';
  const hasScore =
    typeof scorecard.overall_score === 'number' && !Number.isNaN(scorecard.overall_score);

  return (
    <div
      data-testid="evaluation-scorecard"
      style={{
        padding: 16,
        borderRadius: 12,
        background: '#0F1623',
        border: '1px solid #1E2D45',
        color: '#F1F5F9',
        display: 'flex',
        flexDirection: 'column',
        gap: 12,
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: 10,
        }}
      >
        <div>
          <div style={{ fontSize: 14, fontWeight: 700 }}>
            QA Scorecard · {scorecard.status}
          </div>
          <div style={{ fontSize: 12, color: '#94A3B8', marginTop: 2 }}>
            {scorecard.explanation}
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div
            data-testid="scorecard-overall-score"
            style={{
              fontSize: 18,
              fontWeight: 800,
              padding: '4px 12px',
              borderRadius: 8,
              background: isPass
                ? 'rgba(16,185,129,0.16)'
                : 'rgba(239,68,68,0.16)',
              color: isPass ? '#34D399' : '#F87171',
            }}
          >
            {hasScore ? `${Math.round(scorecard.overall_score!)}%` : 'N/A'}
          </div>
          {onRerunEvaluation && (
            <button
              type="button"
              data-testid="scorecard-rerun-btn"
              onClick={onRerunEvaluation}
              disabled={rerunning}
              style={{
                padding: '6px 12px',
                borderRadius: 6,
                border: '1px solid #334155',
                background: '#1E293B',
                color: '#E2E8F0',
                fontSize: 12,
                cursor: rerunning ? 'wait' : 'pointer',
              }}
            >
              {rerunning ? 'Re-evaluating...' : 'Rerun Evaluation'}
            </button>
          )}
        </div>
      </div>

      {/* Per-rule results */}
      {results.length === 0 ? (
        <div style={{ fontSize: 12, color: '#64748B' }}>
          No evaluation assertions were configured for this run (NO_ASSERTIONS).
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {results.map((r) => (
            <div
              key={r.id}
              data-testid={`evaluation-result-row-${r.rule_name}`}
              style={{
                padding: 10,
                borderRadius: 8,
                background: '#0A0E17',
                border: '1px solid #1E2D45',
                fontSize: 12,
              }}
            >
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                }}
              >
                <span style={{ fontWeight: 700 }}>
                  {r.rule_name} <code style={{ color: '#93C5FD' }}>({r.rule_type})</code>
                </span>
                <span
                  style={{
                    fontWeight: 700,
                    color: r.status === 'passed' ? '#34D399' : '#F87171',
                  }}
                >
                  {r.status.toUpperCase()} · {Math.round(r.score * 100)}% (w={r.weight})
                </span>
              </div>
              <div style={{ color: '#94A3B8', marginTop: 4 }}>{r.explanation}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default EvaluationScorecard;
