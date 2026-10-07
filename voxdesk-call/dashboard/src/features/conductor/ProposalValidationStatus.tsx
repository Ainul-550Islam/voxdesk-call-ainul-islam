import React from 'react';
import type { ConductorProposal } from '../../api/types/conductor';

export interface ProposalValidationStatusProps {
  proposal: ConductorProposal | null;
  busyAction?: string | null;
  onValidate: (proposalId: string) => Promise<unknown>;
  onSimulate: (proposalId: string) => Promise<unknown>;
}

export const ProposalValidationStatus: React.FC<
  ProposalValidationStatusProps
> = ({ proposal, busyAction = null, onValidate, onSimulate }) => {
  if (!proposal) return null;

  const valStatus = (proposal.validation_status || 'not_run').toLowerCase();
  const simStatus = (proposal.simulation_status || 'not_run').toLowerCase();
  const valErrors = proposal.validation_report?.errors || [];
  const valWarnings = proposal.validation_report?.warnings || [];
  const simSummary = proposal.simulation_summary || {};
  const isApplied = proposal.status === 'APPLIED';

  return (
    <div
      data-testid="proposal-validation-status"
      style={{
        padding: 16,
        borderRadius: 12,
        background: 'rgba(15, 23, 42, 0.85)',
        border: '1px solid rgba(148, 163, 184, 0.24)',
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
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <h4 style={{ margin: 0, fontSize: 14, color: '#f8fafc' }}>
            Verification & Simulation Gate
          </h4>
          <span
            data-testid="validation-status-badge"
            style={{
              padding: '3px 9px',
              borderRadius: 6,
              fontSize: 11,
              fontWeight: 700,
              textTransform: 'uppercase',
              background:
                valStatus === 'valid'
                  ? 'rgba(16, 185, 129, 0.18)'
                  : valStatus === 'invalid'
                  ? 'rgba(239, 68, 68, 0.2)'
                  : 'rgba(148, 163, 184, 0.18)',
              color:
                valStatus === 'valid'
                  ? '#34d399'
                  : valStatus === 'invalid'
                  ? '#f87171'
                  : '#cbd5e1',
            }}
          >
            VALIDATION: {valStatus.toUpperCase()}
          </span>

          <span
            data-testid="simulation-status-badge"
            style={{
              padding: '3px 9px',
              borderRadius: 6,
              fontSize: 11,
              fontWeight: 700,
              textTransform: 'uppercase',
              background:
                simStatus === 'passed'
                  ? 'rgba(16, 185, 129, 0.18)'
                  : simStatus === 'failed' || simStatus === 'error'
                  ? 'rgba(239, 68, 68, 0.2)'
                  : 'rgba(148, 163, 184, 0.18)',
              color:
                simStatus === 'passed'
                  ? '#34d399'
                  : simStatus === 'failed' || simStatus === 'error'
                  ? '#f87171'
                  : '#cbd5e1',
            }}
          >
            SIMULATION: {simStatus.toUpperCase()}
          </span>

          <span
            data-testid="conductor-provider-badge"
            style={{
              padding: '3px 9px',
              borderRadius: 6,
              fontSize: 11,
              fontWeight: 600,
              background: proposal.is_mock_provider
                ? 'rgba(245, 158, 11, 0.16)'
                : 'rgba(16, 185, 129, 0.16)',
              color: proposal.is_mock_provider ? '#fbbf24' : '#34d399',
            }}
          >
            {proposal.is_mock_provider
              ? `SANDBOX MOCK (${proposal.provider}/${proposal.model})`
              : `LIVE PROVIDER (${proposal.provider}/${proposal.model})`}
          </span>
        </div>

        <div style={{ display: 'flex', gap: 8 }}>
          <button
            type="button"
            data-testid="validate-proposal-btn"
            disabled={isApplied || Boolean(busyAction)}
            onClick={() => void onValidate(proposal.id)}
            style={{
              padding: '6px 12px',
              borderRadius: 8,
              border: '1px solid rgba(59, 130, 246, 0.45)',
              background: 'rgba(59, 130, 246, 0.16)',
              color: '#93c5fd',
              fontSize: 12,
              fontWeight: 600,
              cursor: isApplied ? 'not-allowed' : 'pointer',
            }}
          >
            {busyAction === 'validate' ? 'Validating...' : 'Run Validation'}
          </button>
          <button
            type="button"
            data-testid="simulate-proposal-btn"
            disabled={isApplied || Boolean(busyAction)}
            onClick={() => void onSimulate(proposal.id)}
            style={{
              padding: '6px 12px',
              borderRadius: 8,
              border: '1px solid rgba(139, 92, 246, 0.45)',
              background: 'rgba(139, 92, 246, 0.18)',
              color: '#c4b5fd',
              fontSize: 12,
              fontWeight: 600,
              cursor: isApplied ? 'not-allowed' : 'pointer',
            }}
          >
            {busyAction === 'simulate'
              ? 'Simulating...'
              : 'Simulate Candidate (Prompt 3)'}
          </button>
        </div>
      </div>

      {valErrors.length > 0 && (
        <div
          data-testid="validation-errors-box"
          style={{
            padding: 10,
            borderRadius: 8,
            background: 'rgba(239, 68, 68, 0.14)',
            border: '1px solid rgba(239, 68, 68, 0.35)',
            color: '#fca5a5',
            fontSize: 12,
          }}
        >
          <strong>Validation Errors ({valErrors.length}):</strong>
          <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
            {valErrors.map((err, idx) => (
              <li key={idx}>
                <code>{err.field}</code>: {err.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      {valWarnings.length > 0 && (
        <div
          style={{
            padding: 10,
            borderRadius: 8,
            background: 'rgba(245, 158, 11, 0.12)',
            border: '1px solid rgba(245, 158, 11, 0.3)',
            color: '#fcd34d',
            fontSize: 12,
          }}
        >
          <strong>Warnings ({valWarnings.length}):</strong>
          <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
            {valWarnings.map((w, idx) => (
              <li key={idx}>
                <code>{w.field}</code>: {w.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      {simSummary.test_run_id && (
        <div
          data-testid="simulation-summary-box"
          style={{
            padding: 10,
            borderRadius: 8,
            background: 'rgba(30, 41, 59, 0.75)',
            border: '1px solid rgba(148, 163, 184, 0.2)',
            fontSize: 12,
            color: '#e2e8f0',
          }}
        >
          Linked Simulation TestRun: <code>{simSummary.test_run_id}</code> ·
          Scorecard Status:{' '}
          <strong>
            {String(simSummary.scorecard_summary?.status || 'NO_ASSERTIONS')}
          </strong>
          {simSummary.scorecard_summary?.overall_score !== undefined &&
          simSummary.scorecard_summary?.overall_score !== null
            ? ` · Score: ${simSummary.scorecard_summary.overall_score}%`
            : ''}
          {simSummary.test_case_id ? (
            <span>
              {' '}
              · Reproduction TestCase: <code>{simSummary.test_case_id}</code>
            </span>
          ) : null}
        </div>
      )}
    </div>
  );
};

export default ProposalValidationStatus;
