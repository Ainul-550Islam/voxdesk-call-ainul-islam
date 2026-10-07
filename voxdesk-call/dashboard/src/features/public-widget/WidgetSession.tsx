/**
 * dashboard/src/features/public-widget/WidgetSession.tsx
 * Restricted public widget session surface with explicit state transitions,
 * multi-turn chat transcript, and honest NOT_CONFIGURED voice status disclosure.
 */

import React, { useState } from 'react';
import type {
  PublicWidgetSessionMode,
  PublicWidgetSessionRecord,
  PublicWidgetTurnRecord,
  PublicWidgetUiState,
} from '../../api/types/public-widget';

export interface WidgetSessionProps {
  uiState: PublicWidgetUiState;
  session: PublicWidgetSessionRecord | null;
  transcript: PublicWidgetTurnRecord[];
  errorMessage: string | null;
  sendingMessage: boolean;
  onSendMessage: (message: string) => Promise<void>;
  onEndSession: () => Promise<void>;
  onSwitchMode: (mode: PublicWidgetSessionMode) => Promise<void>;
  onReset: () => void;
}

const STATE_BADGE_STYLES: Record<
  PublicWidgetUiState,
  { label: string; bg: string; fg: string; border: string }
> = {
  IDLE: {
    label: 'IDLE',
    bg: 'rgba(148,163,184,0.12)',
    fg: '#94A3B8',
    border: 'rgba(148,163,184,0.3)',
  },
  VALIDATING_KEY: {
    label: 'VALIDATING KEY',
    bg: 'rgba(59,130,246,0.14)',
    fg: '#60A5FA',
    border: 'rgba(59,130,246,0.35)',
  },
  READY: {
    label: 'READY',
    bg: 'rgba(16,185,129,0.14)',
    fg: '#34D399',
    border: 'rgba(16,185,129,0.35)',
  },
  CONNECTING: {
    label: 'CONNECTING',
    bg: 'rgba(59,130,246,0.14)',
    fg: '#60A5FA',
    border: 'rgba(59,130,246,0.35)',
  },
  CONNECTED: {
    label: 'CONNECTED',
    bg: 'rgba(16,185,129,0.16)',
    fg: '#34D399',
    border: 'rgba(16,185,129,0.4)',
  },
  SPEAKING: {
    label: 'SPEAKING',
    bg: 'rgba(139,92,246,0.16)',
    fg: '#A78BFA',
    border: 'rgba(139,92,246,0.4)',
  },
  LISTENING: {
    label: 'LISTENING',
    bg: 'rgba(14,165,233,0.16)',
    fg: '#38BDF8',
    border: 'rgba(14,165,233,0.4)',
  },
  ENDED: {
    label: 'ENDED',
    bg: 'rgba(148,163,184,0.14)',
    fg: '#CBD5E1',
    border: 'rgba(148,163,184,0.35)',
  },
  EXPIRED: {
    label: 'EXPIRED',
    bg: 'rgba(245,158,11,0.15)',
    fg: '#FBBF24',
    border: 'rgba(245,158,11,0.4)',
  },
  FORBIDDEN_ORIGIN: {
    label: 'FORBIDDEN ORIGIN',
    bg: 'rgba(239,68,68,0.16)',
    fg: '#F87171',
    border: 'rgba(239,68,68,0.4)',
  },
  INVALID_PUBLIC_KEY: {
    label: 'INVALID PUBLIC KEY',
    bg: 'rgba(239,68,68,0.16)',
    fg: '#F87171',
    border: 'rgba(239,68,68,0.4)',
  },
  NOT_CONFIGURED: {
    label: 'NOT CONFIGURED',
    bg: 'rgba(245,158,11,0.16)',
    fg: '#FBBF24',
    border: 'rgba(245,158,11,0.4)',
  },
  RATE_LIMITED: {
    label: 'RATE LIMITED',
    bg: 'rgba(245,158,11,0.16)',
    fg: '#FBBF24',
    border: 'rgba(245,158,11,0.4)',
  },
  ERROR: {
    label: 'ERROR',
    bg: 'rgba(239,68,68,0.16)',
    fg: '#F87171',
    border: 'rgba(239,68,68,0.4)',
  },
};

export function WidgetSession({
  uiState,
  session,
  transcript,
  errorMessage,
  sendingMessage,
  onSendMessage,
  onEndSession,
  onSwitchMode,
  onReset,
}: WidgetSessionProps) {
  const [draftMessage, setDraftMessage] = useState('');
  const badge = STATE_BADGE_STYLES[uiState] || STATE_BADGE_STYLES.IDLE;

  const canSendChat =
    session &&
    session.mode === 'chat' &&
    (uiState === 'CONNECTED' || uiState === 'LISTENING' || uiState === 'SPEAKING') &&
    !sendingMessage;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = draftMessage.trim();
    if (!trimmed || !canSendChat) return;
    setDraftMessage('');
    await onSendMessage(trimmed);
  };

  return (
    <div
      data-testid="widget-session-container"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 12,
        background: '#0F1623',
        border: '1px solid #1E2D45',
        borderRadius: 12,
        padding: 16,
      }}
    >
      {/* Header row with explicit state badge and pinned version */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 8,
          borderBottom: '1px solid #1E2D45',
          paddingBottom: 10,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span
            data-testid="widget-ui-state-badge"
            style={{
              fontSize: 11,
              fontWeight: 700,
              letterSpacing: '0.05em',
              padding: '3px 8px',
              borderRadius: 999,
              background: badge.bg,
              color: badge.fg,
              border: `1px solid ${badge.border}`,
            }}
          >
            {badge.label}
          </span>
          {session && (
            <span
              data-testid="widget-pinned-version-badge"
              style={{
                fontSize: 11,
                color: '#94A3B8',
                fontFamily: 'var(--font-mono, monospace)',
                background: '#141D2E',
                padding: '2px 7px',
                borderRadius: 6,
                border: '1px solid #1E2D45',
              }}
            >
              {session.agent_name} · v{session.agent_version_number} ({session.mode})
            </span>
          )}
        </div>

        {session && uiState !== 'ENDED' && uiState !== 'EXPIRED' && (
          <button
            type="button"
            data-testid="widget-end-session-btn"
            onClick={() => void onEndSession()}
            style={{
              fontSize: 11,
              fontWeight: 600,
              padding: '4px 10px',
              borderRadius: 6,
              border: '1px solid rgba(239,68,68,0.35)',
              background: 'rgba(239,68,68,0.12)',
              color: '#F87171',
              cursor: 'pointer',
            }}
          >
            End Session
          </button>
        )}
      </div>

      {/* Honest NOT_CONFIGURED voice disclosure */}
      {uiState === 'NOT_CONFIGURED' && (
        <div
          data-testid="widget-not-configured-banner"
          style={{
            padding: 12,
            borderRadius: 8,
            background: 'rgba(245,158,11,0.1)',
            border: '1px solid rgba(245,158,11,0.35)',
            color: '#FDE68A',
            fontSize: 12.5,
            lineHeight: 1.5,
          }}
        >
          <div style={{ fontWeight: 700, marginBottom: 4 }}>
            Voice Transport Not Configured (NOT_CONFIGURED)
          </div>
          <div>
            {errorMessage ||
              session?.error_message ||
              'Live WebRTC/SIP voice transport is not configured for this workspace environment.'}
          </div>
          <div style={{ marginTop: 10, display: 'flex', gap: 8 }}>
            <button
              type="button"
              data-testid="widget-fallback-chat-btn"
              onClick={() => void onSwitchMode('chat')}
              style={{
                padding: '6px 12px',
                borderRadius: 6,
                border: '1px solid #2563EB',
                background: '#2563EB',
                color: '#FFFFFF',
                fontSize: 12,
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              Switch to Live Chat Session
            </button>
          </div>
        </div>
      )}

      {/* Explicit security / rate-limit / validation error disclosure */}
      {(uiState === 'FORBIDDEN_ORIGIN' ||
        uiState === 'INVALID_PUBLIC_KEY' ||
        uiState === 'RATE_LIMITED' ||
        uiState === 'EXPIRED' ||
        uiState === 'ERROR') &&
        errorMessage && (
          <div
            data-testid="widget-error-banner"
            style={{
              padding: 12,
              borderRadius: 8,
              background: 'rgba(239,68,68,0.1)',
              border: '1px solid rgba(239,68,68,0.35)',
              color: '#FCA5A5',
              fontSize: 12.5,
              lineHeight: 1.5,
            }}
          >
            <div style={{ fontWeight: 700, marginBottom: 4 }}>{badge.label}</div>
            <div>{errorMessage}</div>
            <button
              type="button"
              onClick={onReset}
              style={{
                marginTop: 8,
                padding: '4px 10px',
                borderRadius: 6,
                border: '1px solid #334155',
                background: '#1E293B',
                color: '#E2E8F0',
                fontSize: 11.5,
                cursor: 'pointer',
              }}
            >
              Reset Widget
            </button>
          </div>
        )}

      {/* Multi-turn conversation transcript */}
      <div
        data-testid="widget-transcript-list"
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
          maxHeight: 260,
          overflowY: 'auto',
          padding: '6px 2px',
        }}
      >
        {transcript.length === 0 ? (
          <div
            style={{
              fontSize: 12,
              color: '#64748B',
              textAlign: 'center',
              padding: '18px 8px',
            }}
          >
            {uiState === 'CONNECTING'
              ? 'Establishing pinned agent session...'
              : 'No conversation turns yet.'}
          </div>
        ) : (
          transcript.map((turn, idx) => {
            const isUser = turn.role === 'user';
            return (
              <div
                key={`${turn.turn_index}-${idx}`}
                data-testid={`widget-turn-${turn.role}`}
                style={{
                  alignSelf: isUser ? 'flex-end' : 'flex-start',
                  maxWidth: '85%',
                  padding: '8px 12px',
                  borderRadius: 10,
                  background: isUser ? '#2563EB' : '#141D2E',
                  color: isUser ? '#FFFFFF' : '#E2E8F0',
                  border: isUser ? 'none' : '1px solid #1E2D45',
                  fontSize: 12.5,
                  lineHeight: 1.45,
                }}
              >
                <div
                  style={{
                    fontSize: 10,
                    opacity: 0.75,
                    marginBottom: 3,
                    fontWeight: 600,
                    textTransform: 'uppercase',
                  }}
                >
                  {isUser ? 'Visitor' : session?.agent_name || 'Agent'}
                </div>
                <div>{turn.content}</div>
              </div>
            );
          })
        )}
      </div>

      {/* Chat composer */}
      {session && session.mode === 'chat' && uiState !== 'ENDED' && uiState !== 'EXPIRED' && (
        <form
          onSubmit={(e) => void handleSubmit(e)}
          style={{ display: 'flex', gap: 8, marginTop: 4 }}
        >
          <input
            type="text"
            data-testid="widget-message-input"
            value={draftMessage}
            onChange={(e) => setDraftMessage(e.target.value)}
            placeholder={
              session.appearance?.placeholder || 'Ask a question about appointments or support...'
            }
            disabled={!canSendChat}
            style={{
              flex: 1,
              padding: '8px 12px',
              borderRadius: 8,
              border: '1px solid #1E2D45',
              background: '#0A0E17',
              color: '#F1F5F9',
              fontSize: 12.5,
              outline: 'none',
            }}
          />
          <button
            type="submit"
            data-testid="widget-send-btn"
            disabled={!canSendChat || !draftMessage.trim()}
            style={{
              padding: '8px 14px',
              borderRadius: 8,
              border: 'none',
              background:
                !canSendChat || !draftMessage.trim()
                  ? '#1E293B'
                  : session.appearance?.primary_color || '#2563EB',
              color: '#FFFFFF',
              fontSize: 12.5,
              fontWeight: 600,
              cursor: !canSendChat || !draftMessage.trim() ? 'not-allowed' : 'pointer',
            }}
          >
            {sendingMessage ? 'Sending...' : 'Send'}
          </button>
        </form>
      )}

      {/* Session footer metadata */}
      {session && (
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            fontSize: 11,
            color: '#64748B',
            borderTop: '1px solid #141D2E',
            paddingTop: 8,
          }}
        >
          <span>
            Turns: {session.turns_count}/{session.max_turns}
          </span>
          <span>Transport: {session.transport}</span>
        </div>
      )}
    </div>
  );
}

export default WidgetSession;
