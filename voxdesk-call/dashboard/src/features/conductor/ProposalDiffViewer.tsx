import React, { useState } from 'react';
import type { ConductorProposalDiff } from '../../api/types/conductor';

export interface ProposalDiffViewerProps {
  diff: ConductorProposalDiff | null;
}

type DiffViewMode = 'side_by_side' | 'inline' | 'json';

export const ProposalDiffViewer: React.FC<ProposalDiffViewerProps> = ({
  diff,
}) => {
  const [viewMode, setViewMode] = useState<DiffViewMode>('side_by_side');

  if (!diff) {
    return (
      <div
        data-testid="proposal-diff-viewer-empty"
        style={{
          padding: 18,
          borderRadius: 10,
          background: 'rgba(15, 23, 42, 0.7)',
          border: '1px solid rgba(148, 163, 184, 0.2)',
          color: '#94a3b8',
          fontSize: 13,
        }}
      >
        Select a proposal to inspect its deterministic configuration diff.
      </div>
    );
  }

  const sections = Object.entries(diff.grouped_by_section || {});

  return (
    <div
      data-testid="proposal-diff-viewer"
      style={{
        padding: 18,
        borderRadius: 12,
        background: 'rgba(15, 23, 42, 0.85)',
        border: '1px solid rgba(148, 163, 184, 0.24)',
        display: 'flex',
        flexDirection: 'column',
        gap: 14,
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
          <h4 style={{ margin: 0, fontSize: 15, color: '#f8fafc' }}>
            Deterministic Configuration Diff · Base v{diff.base_version_number}
          </h4>
          <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 3 }}>
            Base Hash: <code>{diff.base_config_hash.slice(0, 12)}</code> ·
            Candidate Hash: <code>{diff.candidate_config_hash.slice(0, 12)}</code> ·
            Diff Hash: <code>{diff.diff_hash.slice(0, 12)}</code>
          </div>
        </div>

        <div style={{ display: 'flex', gap: 6 }}>
          {(
            [
              { id: 'side_by_side', label: 'Side-by-Side' },
              { id: 'inline', label: 'Unified Line Diff' },
              { id: 'json', label: 'Structured JSON' },
            ] as const
          ).map((tab) => (
            <button
              key={tab.id}
              type="button"
              data-testid={`diff-mode-${tab.id}`}
              onClick={() => setViewMode(tab.id)}
              style={{
                padding: '5px 11px',
                borderRadius: 6,
                border: '1px solid rgba(148, 163, 184, 0.3)',
                background:
                  viewMode === tab.id
                    ? 'rgba(59, 130, 246, 0.24)'
                    : 'rgba(30, 41, 59, 0.8)',
                color: viewMode === tab.id ? '#93c5fd' : '#cbd5e1',
                fontSize: 12,
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {viewMode === 'side_by_side' && (
        <div
          data-testid="diff-side-by-side-view"
          style={{ display: 'flex', flexDirection: 'column', gap: 12 }}
        >
          {sections.map(([sectionName, items]) => (
            <div
              key={sectionName}
              style={{
                padding: 12,
                borderRadius: 8,
                background: 'rgba(30, 41, 59, 0.6)',
                border: '1px solid rgba(148, 163, 184, 0.18)',
              }}
            >
              <div
                style={{
                  fontSize: 12,
                  fontWeight: 700,
                  textTransform: 'uppercase',
                  color: '#93c5fd',
                  marginBottom: 8,
                }}
              >
                Section: {sectionName} ({items.length})
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {items.map((item) => (
                  <div
                    key={item.change_id}
                    data-testid={`diff-item-${item.path}`}
                    style={{
                      display: 'grid',
                      gridTemplateColumns: '180px 1fr 1fr',
                      gap: 10,
                      alignItems: 'start',
                      fontSize: 12,
                      padding: 8,
                      borderRadius: 6,
                      background: 'rgba(15, 23, 42, 0.8)',
                    }}
                  >
                    <div>
                      <code style={{ color: '#f8fafc', fontWeight: 700 }}>
                        {item.path}
                      </code>
                      <div style={{ fontSize: 11, color: '#94a3b8' }}>
                        {item.json_pointer} ({item.operation})
                      </div>
                    </div>
                    <pre
                      style={{
                        margin: 0,
                        padding: 8,
                        borderRadius: 6,
                        background: 'rgba(239, 68, 68, 0.1)',
                        color: '#fca5a5',
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word',
                        fontSize: 11,
                      }}
                    >
                      {JSON.stringify(item.old_value, null, 2)}
                    </pre>
                    <pre
                      style={{
                        margin: 0,
                        padding: 8,
                        borderRadius: 6,
                        background: 'rgba(16, 185, 129, 0.1)',
                        color: '#6ee7b7',
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word',
                        fontSize: 11,
                      }}
                    >
                      {JSON.stringify(item.new_value, null, 2)}
                    </pre>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {viewMode === 'inline' && (
        <div
          data-testid="diff-inline-view"
          style={{ display: 'flex', flexDirection: 'column', gap: 10 }}
        >
          {diff.changes.map((item) => (
            <div
              key={item.change_id}
              style={{
                padding: 10,
                borderRadius: 8,
                background: '#0f172a',
                border: '1px solid rgba(148, 163, 184, 0.2)',
              }}
            >
              <div
                style={{
                  fontSize: 12,
                  fontWeight: 700,
                  color: '#e2e8f0',
                  marginBottom: 6,
                }}
              >
                <code>{item.path}</code> ({item.operation})
              </div>
              <pre
                style={{
                  margin: 0,
                  fontSize: 11,
                  fontFamily: 'monospace',
                  color: '#cbd5e1',
                  whiteSpace: 'pre-wrap',
                }}
              >
                {(item.line_diff || []).join('\n') ||
                  `- ${JSON.stringify(item.old_value)}\n+ ${JSON.stringify(
                    item.new_value
                  )}`}
              </pre>
            </div>
          ))}
        </div>
      )}

      {viewMode === 'json' && (
        <div
          data-testid="diff-json-view"
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: 12,
          }}
        >
          <div>
            <div
              style={{
                fontSize: 11,
                fontWeight: 700,
                color: '#fca5a5',
                marginBottom: 4,
              }}
            >
              BASE SNAPSHOT (v{diff.base_version_number})
            </div>
            <pre
              style={{
                margin: 0,
                padding: 10,
                borderRadius: 8,
                background: '#0f172a',
                color: '#cbd5e1',
                fontSize: 11,
                maxHeight: 300,
                overflow: 'auto',
              }}
            >
              {JSON.stringify(diff.side_by_side?.base_config || {}, null, 2)}
            </pre>
          </div>
          <div>
            <div
              style={{
                fontSize: 11,
                fontWeight: 700,
                color: '#6ee7b7',
                marginBottom: 4,
              }}
            >
              APPROVED CANDIDATE SNAPSHOT
            </div>
            <pre
              style={{
                margin: 0,
                padding: 10,
                borderRadius: 8,
                background: '#0f172a',
                color: '#6ee7b7',
                fontSize: 11,
                maxHeight: 300,
                overflow: 'auto',
              }}
            >
              {JSON.stringify(
                diff.side_by_side?.approved_candidate_config ||
                  diff.side_by_side?.candidate_config ||
                  {},
                null,
                2
              )}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
};

export default ProposalDiffViewer;
