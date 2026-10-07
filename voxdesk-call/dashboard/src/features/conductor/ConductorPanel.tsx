import React, { useState } from 'react';
import { useConductor } from '../../hooks/useConductor';
import { ProposalApprovalBar } from './ProposalApprovalBar';
import { ProposalChangeList } from './ProposalChangeList';
import { ProposalDiffViewer } from './ProposalDiffViewer';
import { ProposalValidationStatus } from './ProposalValidationStatus';

export interface ConductorPanelProps {
  agentId: string;
  agentKind?: 'voice' | 'chat';
  baseVersionNumber?: number;
  originSurface?: 'agent_builder' | 'agent_detail' | 'qa_scorecard' | 'call_detail';
  callIds?: string[];
  testRunIds?: string[];
}

export const ConductorPanel: React.FC<ConductorPanelProps> = ({
  agentId,
  agentKind = 'voice',
  baseVersionNumber,
  originSurface = 'agent_builder',
  callIds = [],
  testRunIds = [],
}) => {
  const {
    activeSession,
    proposals,
    activeProposal,
    activeDiff,
    loading,
    busyAction,
    error,
    refreshAll,
    loadProposalAndDiff,
    createProposal,
    runValidate,
    runSimulate,
    handleApproveChange,
    handleRejectChange,
    handleUndoChange,
    handleApproveProposal,
    handleRejectProposal,
    handleUndoProposal,
    handleApplyProposal,
  } = useConductor({
    agentId,
    agentKind,
    autoLoad: Boolean(agentId),
  });

  const [requestText, setRequestText] = useState('');
  const [autoSimulate, setAutoSimulate] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!requestText.trim() || !agentId) return;
    const created = await createProposal({
      session_id: activeSession?.id,
      agent_id: agentId,
      agent_kind: agentKind,
      base_version_number: baseVersionNumber,
      request_text: requestText.trim(),
      origin_surface: originSurface,
      call_ids: callIds,
      test_run_ids: testRunIds,
      auto_validate: true,
      auto_simulate: autoSimulate,
    });
    if (created) {
      setRequestText('');
    }
  };

  return (
    <div
      data-testid="conductor-panel"
      style={{
        padding: 20,
        borderRadius: 14,
        background: 'rgba(15, 23, 42, 0.92)',
        border: '1px solid rgba(148, 163, 184, 0.25)',
        display: 'flex',
        flexDirection: 'column',
        gap: 18,
      }}
    >
      {/* Copilot Header */}
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
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <h3 style={{ margin: 0, fontSize: 18, color: '#f8fafc' }}>
              Conductor AI Control Plane
            </h3>
            <span
              data-testid="conductor-surface-badge"
              style={{
                padding: '3px 9px',
                borderRadius: 6,
                fontSize: 11,
                fontWeight: 700,
                background: 'rgba(59, 130, 246, 0.2)',
                color: '#93c5fd',
              }}
            >
              SURFACE: {originSurface.toUpperCase()}
            </span>
          </div>
          <p style={{ margin: '4px 0 0', fontSize: 12, color: '#94a3b8' }}>
            Conductor may propose; you must approve. Approved changes create a
            new immutable AgentVersion without auto-modifying live production.
          </p>
        </div>

        {proposals.length > 1 && (
          <select
            aria-label="Select Conductor Proposal"
            data-testid="conductor-proposal-selector"
            value={activeProposal?.id || ''}
            onChange={(e) => {
              if (e.target.value) {
                void loadProposalAndDiff(e.target.value);
              }
            }}
            style={{
              padding: '6px 10px',
              borderRadius: 8,
              border: '1px solid rgba(148, 163, 184, 0.3)',
              background: '#0f172a',
              color: '#f8fafc',
              fontSize: 12,
            }}
          >
            {proposals.map((p) => (
              <option key={p.id} value={p.id}>
                v{p.base_version_number} · {p.status} · {p.summary.slice(0, 48)}
              </option>
            ))}
          </select>
        )}
      </div>

      {error && (
        <div
          data-testid="conductor-error-banner"
          style={{
            padding: 12,
            borderRadius: 8,
            background: 'rgba(239, 68, 68, 0.16)',
            border: '1px solid rgba(239, 68, 68, 0.4)',
            color: '#fca5a5',
            fontSize: 12,
          }}
        >
          {error}
        </div>
      )}

      {/* Natural Language Request Form */}
      <form
        onSubmit={handleSubmit}
        style={{ display: 'flex', flexDirection: 'column', gap: 10 }}
      >
        <textarea
          aria-label="Conductor Natural Language Request"
          data-testid="conductor-request-input"
          rows={3}
          placeholder="Describe what to build, review, test, or improve (e.g., 'Change greeting to Welcome to Dhaka Clinic, slow down voice speed to 0.92, and add book_appointment tool')..."
          value={requestText}
          onChange={(e) => setRequestText(e.target.value)}
          style={{
            padding: 12,
            borderRadius: 10,
            border: '1px solid rgba(148, 163, 184, 0.3)',
            background: '#0f172a',
            color: '#f8fafc',
            fontSize: 13,
            resize: 'vertical',
          }}
        />
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            flexWrap: 'wrap',
            gap: 10,
          }}
        >
          <label
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              fontSize: 12,
              color: '#cbd5e1',
            }}
          >
            <input
              type="checkbox"
              checked={autoSimulate}
              onChange={(e) => setAutoSimulate(e.target.checked)}
            />
            Run Prompt 3 simulation automatically after generating proposal
          </label>
          <button
            type="submit"
            data-testid="submit-conductor-request-btn"
            disabled={
              Boolean(busyAction) || loading || !requestText.trim() || !agentId
            }
            style={{
              padding: '8px 18px',
              borderRadius: 8,
              border: 'none',
              background: 'linear-gradient(135deg, #3b82f6, #6366f1)',
              color: '#ffffff',
              fontSize: 13,
              fontWeight: 700,
              cursor: 'pointer',
            }}
          >
            {busyAction === 'propose'
              ? 'Synthesizing Proposal...'
              : 'Propose Changes'}
          </button>
        </div>
      </form>

      {/* Empty State when no proposal exists */}
      {!activeProposal && !loading && (
        <div
          data-testid="conductor-no-proposal-state"
          style={{
            padding: 20,
            borderRadius: 10,
            background: 'rgba(30, 41, 59, 0.5)',
            border: '1px dashed rgba(148, 163, 184, 0.3)',
            color: '#94a3b8',
            fontSize: 13,
            textAlign: 'center',
          }}
        >
          No active proposal yet. Submit a natural-language instruction above to
          generate a reviewable, version-pinned proposal.
        </div>
      )}

      {/* Active Proposal Workspace */}
      {activeProposal && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <ProposalValidationStatus
            proposal={activeProposal}
            busyAction={busyAction}
            onValidate={runValidate}
            onSimulate={(pid) => runSimulate(pid)}
          />

          <ProposalApprovalBar
            proposal={activeProposal}
            busyAction={busyAction}
            onApproveAll={handleApproveProposal}
            onRejectAll={handleRejectProposal}
            onUndoAll={handleUndoProposal}
            onApply={handleApplyProposal}
            onReloadStale={() => void refreshAll(agentId)}
          />

          <div>
            <h4 style={{ margin: '0 0 10px', fontSize: 14, color: '#e2e8f0' }}>
              Granular Proposed Changes ({activeProposal.changes.length})
            </h4>
            <ProposalChangeList
              proposalId={activeProposal.id}
              changes={activeProposal.changes}
              disabled={
                activeProposal.status === 'APPLIED' ||
                activeProposal.status === 'STALE'
              }
              busyAction={busyAction}
              onApproveChange={handleApproveChange}
              onRejectChange={handleRejectChange}
              onUndoChange={handleUndoChange}
            />
          </div>

          <ProposalDiffViewer diff={activeDiff} />

          {activeProposal.evidence?.length > 0 && (
            <div
              data-testid="proposal-evidence-section"
              style={{
                padding: 14,
                borderRadius: 10,
                background: 'rgba(30, 41, 59, 0.6)',
                border: '1px solid rgba(148, 163, 184, 0.2)',
              }}
            >
              <h4
                style={{ margin: '0 0 8px', fontSize: 13, color: '#e2e8f0' }}
              >
                Linked Proposal Evidence ({activeProposal.evidence.length})
              </h4>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {activeProposal.evidence.map((ev) => (
                  <div
                    key={ev.id}
                    style={{
                      fontSize: 12,
                      color: '#cbd5e1',
                      padding: '6px 10px',
                      borderRadius: 6,
                      background: 'rgba(15, 23, 42, 0.8)',
                    }}
                  >
                    <code style={{ color: '#93c5fd' }}>
                      [{ev.source_type}]
                    </code>{' '}
                    <strong>{ev.source_id}</strong> — {ev.evidence_summary}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default ConductorPanel;
