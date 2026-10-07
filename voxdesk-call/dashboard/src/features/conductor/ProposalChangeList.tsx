import React from 'react';
import type { ConductorChange } from '../../api/types/conductor';

export interface ProposalChangeListProps {
  proposalId: string;
  changes: ConductorChange[];
  disabled?: boolean;
  busyAction?: string | null;
  onApproveChange: (proposalId: string, changeId: string) => Promise<unknown>;
  onRejectChange: (proposalId: string, changeId: string) => Promise<unknown>;
  onUndoChange: (proposalId: string, changeId: string) => Promise<unknown>;
}

const riskBadgeColors: Record<string, { bg: string; fg: string }> = {
  low: { bg: 'rgba(16, 185, 129, 0.16)', fg: '#34d399' },
  medium: { bg: 'rgba(245, 158, 11, 0.18)', fg: '#fbbf24' },
  high: { bg: 'rgba(239, 68, 68, 0.18)', fg: '#f87171' },
};

const approvalBadgeColors: Record<string, { bg: string; fg: string }> = {
  approved: { bg: 'rgba(16, 185, 129, 0.2)', fg: '#34d399' },
  rejected: { bg: 'rgba(239, 68, 68, 0.2)', fg: '#f87171' },
  pending: { bg: 'rgba(148, 163, 184, 0.2)', fg: '#cbd5e1' },
};

export const ProposalChangeList: React.FC<ProposalChangeListProps> = ({
  proposalId,
  changes,
  disabled = false,
  busyAction = null,
  onApproveChange,
  onRejectChange,
  onUndoChange,
}) => {
  const ordered = [...(changes || [])].sort((a, b) => a.sequence - b.sequence);

  if (ordered.length === 0) {
    return (
      <div
        data-testid="proposal-change-list-empty"
        style={{
          padding: 18,
          borderRadius: 10,
          background: 'rgba(15, 23, 42, 0.7)',
          border: '1px solid rgba(148, 163, 184, 0.2)',
          color: '#94a3b8',
          fontSize: 13,
        }}
      >
        No proposed configuration operations yet.
      </div>
    );
  }

  return (
    <div
      data-testid="proposal-change-list"
      style={{ display: 'flex', flexDirection: 'column', gap: 10 }}
    >
      {ordered.map((chg) => {
        const riskTone =
          riskBadgeColors[chg.risk_level] || riskBadgeColors.low;
        const appTone =
          approvalBadgeColors[chg.approval_state] ||
          approvalBadgeColors.pending;
        const isApplied = Boolean(chg.applied_at);
        const rowBusy = Boolean(busyAction);

        return (
          <div
            key={chg.id}
            data-testid={`proposal-change-item-${chg.sequence}`}
            style={{
              padding: 14,
              borderRadius: 10,
              background: 'rgba(15, 23, 42, 0.9)',
              border:
                chg.approval_state === 'approved'
                  ? '1px solid rgba(16, 185, 129, 0.4)'
                  : chg.approval_state === 'rejected'
                  ? '1px solid rgba(239, 68, 68, 0.35)'
                  : '1px solid rgba(148, 163, 184, 0.25)',
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
            }}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                flexWrap: 'wrap',
                gap: 8,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span
                  style={{
                    padding: '2px 8px',
                    borderRadius: 6,
                    fontSize: 11,
                    fontWeight: 700,
                    textTransform: 'uppercase',
                    background: 'rgba(59, 130, 246, 0.18)',
                    color: '#93c5fd',
                  }}
                >
                  {chg.section}
                </span>
                <code
                  data-testid={`change-path-${chg.sequence}`}
                  style={{
                    fontSize: 13,
                    fontWeight: 700,
                    color: '#f8fafc',
                  }}
                >
                  {chg.path}
                </code>
                <span
                  style={{
                    padding: '2px 7px',
                    borderRadius: 5,
                    fontSize: 11,
                    fontWeight: 700,
                    background: 'rgba(139, 92, 246, 0.18)',
                    color: '#c4b5fd',
                    fontFamily: 'monospace',
                  }}
                >
                  {chg.operation.toUpperCase()}
                </span>
                <span
                  data-testid={`change-risk-${chg.sequence}`}
                  style={{
                    padding: '2px 8px',
                    borderRadius: 6,
                    fontSize: 11,
                    fontWeight: 700,
                    textTransform: 'uppercase',
                    background: riskTone.bg,
                    color: riskTone.fg,
                  }}
                >
                  RISK: {chg.risk_level}
                </span>
                <span
                  data-testid={`change-approval-badge-${chg.sequence}`}
                  style={{
                    padding: '2px 8px',
                    borderRadius: 6,
                    fontSize: 11,
                    fontWeight: 800,
                    textTransform: 'uppercase',
                    background: appTone.bg,
                    color: appTone.fg,
                  }}
                >
                  {isApplied ? 'APPLIED' : chg.approval_state.toUpperCase()}
                </span>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <button
                  type="button"
                  data-testid={`accept-change-btn-${chg.sequence}`}
                  disabled={disabled || isApplied || rowBusy || chg.approval_state === 'approved'}
                  onClick={() => void onApproveChange(proposalId, chg.id)}
                  style={{
                    padding: '5px 11px',
                    borderRadius: 6,
                    border: '1px solid rgba(16, 185, 129, 0.45)',
                    background:
                      chg.approval_state === 'approved'
                        ? 'rgba(16, 185, 129, 0.28)'
                        : 'rgba(16, 185, 129, 0.12)',
                    color: '#6ee7b7',
                    fontSize: 12,
                    fontWeight: 600,
                    cursor:
                      disabled || isApplied ? 'not-allowed' : 'pointer',
                  }}
                >
                  Accept
                </button>
                <button
                  type="button"
                  data-testid={`reject-change-btn-${chg.sequence}`}
                  disabled={disabled || isApplied || rowBusy || chg.approval_state === 'rejected'}
                  onClick={() => void onRejectChange(proposalId, chg.id)}
                  style={{
                    padding: '5px 11px',
                    borderRadius: 6,
                    border: '1px solid rgba(239, 68, 68, 0.45)',
                    background:
                      chg.approval_state === 'rejected'
                        ? 'rgba(239, 68, 68, 0.28)'
                        : 'rgba(239, 68, 68, 0.12)',
                    color: '#fca5a5',
                    fontSize: 12,
                    fontWeight: 600,
                    cursor:
                      disabled || isApplied ? 'not-allowed' : 'pointer',
                  }}
                >
                  Reject
                </button>
                {chg.approval_state !== 'pending' && !isApplied && (
                  <button
                    type="button"
                    data-testid={`undo-change-btn-${chg.sequence}`}
                    disabled={disabled || rowBusy}
                    onClick={() => void onUndoChange(proposalId, chg.id)}
                    style={{
                      padding: '5px 10px',
                      borderRadius: 6,
                      border: '1px solid rgba(148, 163, 184, 0.35)',
                      background: 'rgba(30, 41, 59, 0.85)',
                      color: '#cbd5e1',
                      fontSize: 12,
                      cursor: 'pointer',
                    }}
                  >
                    Undo
                  </button>
                )}
              </div>
            </div>

            <div style={{ fontSize: 12, color: '#cbd5e1' }}>{chg.reason}</div>

            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '1fr 1fr',
                gap: 10,
                fontSize: 12,
                fontFamily: 'monospace',
              }}
            >
              <div
                data-testid={`change-old-val-${chg.sequence}`}
                style={{
                  padding: 8,
                  borderRadius: 6,
                  background: 'rgba(239, 68, 68, 0.08)',
                  border: '1px solid rgba(239, 68, 68, 0.2)',
                  color: '#fca5a5',
                  overflowX: 'auto',
                }}
              >
                <div style={{ fontSize: 10, color: '#94a3b8', marginBottom: 2 }}>
                  OLD VALUE
                </div>
                {JSON.stringify(chg.old_value)}
              </div>
              <div
                data-testid={`change-new-val-${chg.sequence}`}
                style={{
                  padding: 8,
                  borderRadius: 6,
                  background: 'rgba(16, 185, 129, 0.08)',
                  border: '1px solid rgba(16, 185, 129, 0.2)',
                  color: '#6ee7b7',
                  overflowX: 'auto',
                }}
              >
                <div style={{ fontSize: 10, color: '#94a3b8', marginBottom: 2 }}>
                  PROPOSED VALUE
                </div>
                {JSON.stringify(chg.new_value)}
              </div>
            </div>

            <div
              style={{
                display: 'flex',
                gap: 12,
                fontSize: 11,
                color: '#94a3b8',
                flexWrap: 'wrap',
              }}
            >
              <span>
                Validation: <strong>{chg.validation_state.toUpperCase()}</strong>
              </span>
              <span>
                Simulation: <strong>{chg.simulation_state.toUpperCase()}</strong>
              </span>
              {chg.evidence_ids?.length > 0 && (
                <span>
                  Evidence Links: <code>{chg.evidence_ids.length}</code>
                </span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
};

export default ProposalChangeList;
