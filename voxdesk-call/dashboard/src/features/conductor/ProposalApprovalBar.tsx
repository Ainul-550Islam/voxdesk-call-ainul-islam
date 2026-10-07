import React, { useState } from 'react';
import type {
  ConductorApplyPayload,
  ConductorProposal,
} from '../../api/types/conductor';

export interface ProposalApprovalBarProps {
  proposal: ConductorProposal | null;
  busyAction?: string | null;
  onApproveAll: (
    proposalId: string,
    options?: { reason?: string; safe_only?: boolean }
  ) => Promise<unknown>;
  onRejectAll: (proposalId: string, reason?: string) => Promise<unknown>;
  onUndoAll: (proposalId: string, reason?: string) => Promise<unknown>;
  onApply: (
    proposalId: string,
    payload?: ConductorApplyPayload
  ) => Promise<unknown>;
  onReloadStale?: () => void;
}

export const ProposalApprovalBar: React.FC<ProposalApprovalBarProps> = ({
  proposal,
  busyAction = null,
  onApproveAll,
  onRejectAll,
  onUndoAll,
  onApply,
  onReloadStale,
}) => {
  const [versionNotes, setVersionNotes] = useState('');

  if (!proposal) return null;

  const changes = proposal.changes || [];
  const approvedCount = changes.filter(
    (c) => c.approval_state === 'approved'
  ).length;
  const rejectedCount = changes.filter(
    (c) => c.approval_state === 'rejected'
  ).length;
  const pendingCount = changes.filter(
    (c) => c.approval_state === 'pending'
  ).length;

  const isStale = proposal.status === 'STALE';
  const isApplied = proposal.status === 'APPLIED';
  const isBusy = Boolean(busyAction);
  const canApply =
    !isApplied &&
    !isStale &&
    approvedCount > 0 &&
    proposal.validation_status === 'valid' &&
    (proposal.status === 'APPROVED' ||
      proposal.status === 'PARTIALLY_APPROVED');

  return (
    <div
      data-testid="proposal-approval-bar"
      style={{
        padding: 16,
        borderRadius: 12,
        background: 'rgba(15, 23, 42, 0.9)',
        border: isStale
          ? '1px solid rgba(245, 158, 11, 0.55)'
          : isApplied
          ? '1px solid rgba(16, 185, 129, 0.5)'
          : '1px solid rgba(148, 163, 184, 0.25)',
        display: 'flex',
        flexDirection: 'column',
        gap: 12,
      }}
    >
      {/* Production Safety Lifecycle Stepper */}
      <div
        data-testid="conductor-lifecycle-stepper"
        style={{
          display: 'flex',
          gap: 8,
          flexWrap: 'wrap',
          fontSize: 11,
          fontWeight: 700,
        }}
      >
        {[
          { label: 'Candidate Created', done: true },
          {
            label: 'Validation Passed',
            done: proposal.validation_status === 'valid',
          },
          {
            label: 'Simulation Passed',
            done: proposal.simulation_status === 'passed',
          },
          { label: 'Human Approved', done: approvedCount > 0 },
          {
            label: proposal.resulting_version_number
              ? `New Version Created (v${proposal.resulting_version_number})`
              : 'New Version Created',
            done: isApplied,
          },
          { label: 'Ready to Publish', done: isApplied },
          {
            label: 'Production Published (Separate Action Required)',
            done: Boolean(proposal.production_published),
          },
        ].map((step, i) => (
          <span
            key={i}
            style={{
              padding: '3px 9px',
              borderRadius: 6,
              background: step.done
                ? 'rgba(16, 185, 129, 0.18)'
                : 'rgba(30, 41, 59, 0.85)',
              color: step.done ? '#34d399' : '#94a3b8',
              border: step.done
                ? '1px solid rgba(16, 185, 129, 0.35)'
                : '1px solid rgba(148, 163, 184, 0.2)',
            }}
          >
            {step.done ? '✓ ' : '○ '}
            {step.label}
          </span>
        ))}
      </div>

      {/* Stale Conflict Warning Banner */}
      {isStale && (
        <div
          data-testid="stale-proposal-banner"
          style={{
            padding: 12,
            borderRadius: 8,
            background: 'rgba(245, 158, 11, 0.16)',
            border: '1px solid rgba(245, 158, 11, 0.45)',
            color: '#fcd34d',
            fontSize: 12,
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            gap: 12,
          }}
        >
          <div>
            <strong>STALE VERSION CONFLICT:</strong>{' '}
            {proposal.error_message ||
              `Base version v${proposal.base_version_number} is no longer the latest version. Applying is blocked to prevent overwriting newer changes.`}
          </div>
          {onReloadStale && (
            <button
              type="button"
              data-testid="reload-stale-btn"
              onClick={onReloadStale}
              style={{
                padding: '6px 12px',
                borderRadius: 6,
                border: '1px solid rgba(245, 158, 11, 0.5)',
                background: 'rgba(245, 158, 11, 0.25)',
                color: '#fef3c7',
                fontSize: 12,
                fontWeight: 700,
                cursor: 'pointer',
              }}
            >
              Reload Latest Context
            </button>
          )}
        </div>
      )}

      {/* Applied Banner */}
      {isApplied && (
        <div
          data-testid="applied-version-banner"
          style={{
            padding: 12,
            borderRadius: 8,
            background: 'rgba(16, 185, 129, 0.16)',
            border: '1px solid rgba(16, 185, 129, 0.4)',
            color: '#6ee7b7',
            fontSize: 12,
          }}
        >
          <strong>Immutable AgentVersion v{proposal.resulting_version_number} Created!</strong>{' '}
          (Hash: <code>{proposal.final_candidate_hash.slice(0, 12)}</code>).{' '}
          <span data-testid="production-safety-notice">
            Production was NOT auto-modified — use the explicit Agent Publish
            action when ready to deploy to production.
          </span>
        </div>
      )}

      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: 12,
        }}
      >
        <div style={{ fontSize: 12, color: '#cbd5e1' }}>
          Proposal Status:{' '}
          <strong data-testid="proposal-status-text">{proposal.status}</strong>{' '}
          · Approved: <strong>{approvedCount}</strong> · Rejected:{' '}
          <strong>{rejectedCount}</strong> · Pending:{' '}
          <strong>{pendingCount}</strong>
        </div>

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button
            type="button"
            data-testid="accept-safe-changes-btn"
            disabled={isApplied || isStale || isBusy}
            onClick={() =>
              void onApproveAll(proposal.id, {
                reason: 'Accepted all low-risk changes',
                safe_only: true,
              })
            }
            style={{
              padding: '6px 12px',
              borderRadius: 8,
              border: '1px solid rgba(16, 185, 129, 0.4)',
              background: 'rgba(16, 185, 129, 0.14)',
              color: '#6ee7b7',
              fontSize: 12,
              fontWeight: 600,
              cursor: isApplied || isStale ? 'not-allowed' : 'pointer',
            }}
          >
            Accept Safe Changes
          </button>

          <button
            type="button"
            data-testid="accept-all-changes-btn"
            disabled={isApplied || isStale || isBusy}
            onClick={() =>
              void onApproveAll(proposal.id, {
                reason: 'Accepted all proposed changes',
                safe_only: false,
              })
            }
            style={{
              padding: '6px 12px',
              borderRadius: 8,
              border: '1px solid rgba(16, 185, 129, 0.5)',
              background: 'rgba(16, 185, 129, 0.24)',
              color: '#34d399',
              fontSize: 12,
              fontWeight: 700,
              cursor: isApplied || isStale ? 'not-allowed' : 'pointer',
            }}
          >
            Accept All
          </button>

          <button
            type="button"
            data-testid="reject-all-changes-btn"
            disabled={isApplied || isStale || isBusy}
            onClick={() =>
              void onRejectAll(proposal.id, 'Rejected all pending changes')
            }
            style={{
              padding: '6px 12px',
              borderRadius: 8,
              border: '1px solid rgba(239, 68, 68, 0.45)',
              background: 'rgba(239, 68, 68, 0.15)',
              color: '#fca5a5',
              fontSize: 12,
              fontWeight: 600,
              cursor: isApplied || isStale ? 'not-allowed' : 'pointer',
            }}
          >
            Reject All
          </button>

          {approvedCount > 0 && !isApplied && (
            <button
              type="button"
              data-testid="undo-all-approvals-btn"
              disabled={isStale || isBusy}
              onClick={() =>
                void onUndoAll(proposal.id, 'Undid pending approvals')
              }
              style={{
                padding: '6px 12px',
                borderRadius: 8,
                border: '1px solid rgba(148, 163, 184, 0.4)',
                background: 'rgba(30, 41, 59, 0.9)',
                color: '#e2e8f0',
                fontSize: 12,
                cursor: 'pointer',
              }}
            >
              Undo Approvals
            </button>
          )}
        </div>
      </div>

      {/* Explicit Apply Control Row */}
      {!isApplied && (
        <div
          style={{
            display: 'flex',
            gap: 10,
            alignItems: 'center',
            flexWrap: 'wrap',
            paddingTop: 8,
            borderTop: '1px solid rgba(148, 163, 184, 0.18)',
          }}
        >
          <input
            type="text"
            aria-label="New AgentVersion Notes"
            placeholder="Optional version notes for new immutable AgentVersion..."
            value={versionNotes}
            onChange={(e) => setVersionNotes(e.target.value)}
            style={{
              flex: 1,
              minWidth: 240,
              padding: '8px 12px',
              borderRadius: 8,
              border: '1px solid rgba(148, 163, 184, 0.3)',
              background: '#0f172a',
              color: '#f8fafc',
              fontSize: 12,
            }}
          />
          <button
            type="button"
            data-testid="apply-approved-proposal-btn"
            disabled={!canApply || isBusy}
            onClick={() =>
              void onApply(proposal.id, {
                expected_base_version_number: proposal.base_version_number,
                expected_base_config_hash: proposal.base_config_hash,
                version_notes: versionNotes.trim() || undefined,
              })
            }
            style={{
              padding: '8px 18px',
              borderRadius: 8,
              border: 'none',
              background: canApply
                ? 'linear-gradient(135deg, #10b981, #059669)'
                : 'rgba(51, 65, 85, 0.7)',
              color: canApply ? '#ffffff' : '#94a3b8',
              fontSize: 13,
              fontWeight: 700,
              cursor: canApply ? 'pointer' : 'not-allowed',
            }}
          >
            {busyAction === 'apply_proposal'
              ? 'Creating Immutable Version...'
              : `Apply ${approvedCount} Approved Change(s) → New AgentVersion`}
          </button>
        </div>
      )}
    </div>
  );
};

export default ProposalApprovalBar;
