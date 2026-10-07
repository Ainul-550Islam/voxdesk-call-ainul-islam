/**
 * dashboard/src/components/testing/TranscriptEvidence.tsx
 * Renders persisted transcript turns, tool calls, and event evidence for a TestRun.
 */

import React from 'react';
import type {
  SimulationEventEntry,
  SimulationTranscriptTurn,
} from '../../api/types/test-run';

export interface TranscriptEvidenceProps {
  transcript: SimulationTranscriptTurn[];
  events?: SimulationEventEntry[];
  highlightedTurnIndices?: number[];
}

export function TranscriptEvidence({
  transcript,
  events = [],
  highlightedTurnIndices = [],
}: TranscriptEvidenceProps) {
  return (
    <div
      data-testid="transcript-evidence"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
        padding: 14,
        borderRadius: 12,
        background: '#0F1623',
        border: '1px solid #1E2D45',
        color: '#F1F5F9',
      }}
    >
      <div style={{ fontSize: 13.5, fontWeight: 700 }}>
        Persisted Transcript & Tool Evidence ({transcript.length} turns)
      </div>

      {transcript.length === 0 ? (
        <div style={{ fontSize: 12, color: '#64748B' }}>
          No transcript turns recorded for this test run.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {transcript.map((turn, idx) => {
            const turnIdx = turn.turn_index ?? idx;
            const isHighlighted = highlightedTurnIndices.includes(turnIdx);
            return (
              <div
                key={`${turnIdx}-${idx}`}
                data-testid={`transcript-turn-${turnIdx}`}
                style={{
                  padding: '8px 12px',
                  borderRadius: 8,
                  background: isHighlighted
                    ? 'rgba(59,130,246,0.16)'
                    : '#0A0E17',
                  border: isHighlighted
                    ? '1px solid #3B82F6'
                    : '1px solid #1E2D45',
                  fontSize: 12.5,
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    fontSize: 11,
                    color: '#94A3B8',
                    marginBottom: 4,
                  }}
                >
                  <span style={{ fontWeight: 700, textTransform: 'uppercase' }}>
                    #{turnIdx} · {turn.role}
                  </span>
                  {typeof turn.latency_ms === 'number' && (
                    <span>{turn.latency_ms} ms</span>
                  )}
                </div>
                <div>{turn.content}</div>
              </div>
            );
          })}
        </div>
      )}

      {events.length > 0 && (
        <div style={{ marginTop: 6 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: '#94A3B8', marginBottom: 4 }}>
            Execution Events ({events.length})
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {events.map((ev, idx) => (
              <code
                key={idx}
                style={{
                  fontSize: 11,
                  padding: '2px 7px',
                  borderRadius: 6,
                  background: '#0A0E17',
                  border: '1px solid #1E2D45',
                  color: '#93C5FD',
                }}
              >
                {ev.event}
              </code>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default TranscriptEvidence;
