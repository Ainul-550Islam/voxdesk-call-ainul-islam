import React, { useState } from 'react';
import type {
  CallTestReadiness,
  TestRun,
  WebCallSessionEventPayload,
  WebCallSessionPayload,
} from '../../types/evaluation';

export interface WebCallTesterProps {
  agentId: string;
  agentVersionNumber: number;
  readiness: CallTestReadiness | null;
  activeSession: TestRun | null;
  busy?: boolean;
  onStartSession: (payload: WebCallSessionPayload) => Promise<TestRun | null>;
  onSendEvent: (
    runId: string,
    payload: WebCallSessionEventPayload
  ) => Promise<TestRun | null>;
}

export const WebCallTester: React.FC<WebCallTesterProps> = ({
  agentId,
  agentVersionNumber,
  readiness,
  activeSession,
  busy = false,
  onStartSession,
  onSendEvent,
}) => {
  const [utterance, setUtterance] = useState('');
  const [dtmfDigits, setDtmfDigits] = useState('');
  const [requireLiveWebrtc, setRequireLiveWebrtc] = useState(false);

  const isRunning = activeSession?.status === 'running';

  const handleStart = async () => {
    await onStartSession({
      agent_id: agentId,
      agent_version_number: agentVersionNumber,
      require_live_webrtc: requireLiveWebrtc,
    });
  };

  const handleSendUtterance = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeSession || !utterance.trim()) return;
    const text = utterance.trim();
    setUtterance('');
    await onSendEvent(activeSession.id, {
      event: 'user_utterance',
      text,
    });
  };

  return (
    <div
      data-testid="web-call-tester"
      style={{
        background: 'rgba(15, 23, 42, 0.78)',
        border: '1px solid rgba(148, 163, 184, 0.2)',
        borderRadius: 12,
        padding: 20,
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: 10,
          flexWrap: 'wrap',
          marginBottom: 14,
        }}
      >
        <div>
          <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: '#f8fafc' }}>
            Browser Web Call Tester
          </h3>
          <p style={{ margin: '4px 0 0', fontSize: 12, color: '#94a3b8' }}>
            Pinned to Agent <code>{agentId}</code> · <strong>v{agentVersionNumber}</strong>
          </p>
        </div>

        <span
          data-testid="webrtc-readiness-badge"
          style={{
            padding: '4px 10px',
            borderRadius: 999,
            fontSize: 11,
            fontWeight: 700,
            background: readiness?.webrtc_live_configured
              ? 'rgba(16, 185, 129, 0.16)'
              : 'rgba(245, 158, 11, 0.16)',
            color: readiness?.webrtc_live_configured ? '#34d399' : '#fbbf24',
          }}
        >
          {readiness?.webrtc_live_configured
            ? 'WEBRTC LIVE CONFIGURED'
            : 'SIMULATED WEB AUDIO (WEBRTC UNCONFIGURED)'}
        </span>
      </div>

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          flexWrap: 'wrap',
          marginBottom: 14,
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
            checked={requireLiveWebrtc}
            onChange={(e) => setRequireLiveWebrtc(e.target.checked)}
          />
          Require live WebRTC media bridge (fail with NOT_RUN if unconfigured)
        </label>

        {!isRunning ? (
          <button
            type="button"
            data-testid="start-web-call-btn"
            disabled={busy || !agentId}
            onClick={() => void handleStart()}
            style={{
              padding: '8px 16px',
              borderRadius: 8,
              border: 'none',
              background: '#10b981',
              color: '#ffffff',
              fontWeight: 700,
              fontSize: 13,
              cursor: busy ? 'not-allowed' : 'pointer',
            }}
          >
            {busy ? 'Starting...' : 'Start Web Call Session'}
          </button>
        ) : (
          <>
            <button
              type="button"
              data-testid="interrupt-web-call-btn"
              disabled={busy}
              onClick={() =>
                activeSession &&
                void onSendEvent(activeSession.id, { event: 'interrupt' })
              }
              style={{
                padding: '7px 12px',
                borderRadius: 8,
                border: '1px solid rgba(245, 158, 11, 0.4)',
                background: 'rgba(245, 158, 11, 0.16)',
                color: '#fbbf24',
                fontWeight: 600,
                fontSize: 12,
                cursor: 'pointer',
              }}
            >
              Barge-in / Interrupt
            </button>

            <button
              type="button"
              data-testid="end-web-call-btn"
              disabled={busy}
              onClick={() =>
                activeSession &&
                void onSendEvent(activeSession.id, { event: 'complete' })
              }
              style={{
                padding: '7px 14px',
                borderRadius: 8,
                border: 'none',
                background: '#ef4444',
                color: '#ffffff',
                fontWeight: 700,
                fontSize: 12,
                cursor: 'pointer',
              }}
            >
              End & Evaluate Call
            </button>
          </>
        )}
      </div>

      {isRunning && activeSession && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <form
            onSubmit={handleSendUtterance}
            style={{ display: 'flex', gap: 8 }}
          >
            <input
              type="text"
              aria-label="Web Call User Utterance"
              placeholder="Speak / type caller utterance (e.g. I want to book an appointment tomorrow)..."
              value={utterance}
              onChange={(e) => setUtterance(e.target.value)}
              style={{
                flex: 1,
                padding: '8px 12px',
                borderRadius: 8,
                border: '1px solid rgba(148, 163, 184, 0.3)',
                background: '#0f172a',
                color: '#f8fafc',
                fontSize: 13,
              }}
            />
            <button
              type="submit"
              data-testid="send-web-call-utterance-btn"
              disabled={busy || !utterance.trim()}
              style={{
                padding: '8px 16px',
                borderRadius: 8,
                border: 'none',
                background: '#3b82f6',
                color: '#ffffff',
                fontWeight: 600,
                fontSize: 13,
                cursor: 'pointer',
              }}
            >
              Send Utterance
            </button>
          </form>

          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input
              type="text"
              aria-label="DTMF Digits"
              placeholder="DTMF digits (e.g. 1#)"
              value={dtmfDigits}
              onChange={(e) => setDtmfDigits(e.target.value)}
              style={{
                width: 160,
                padding: '6px 10px',
                borderRadius: 8,
                border: '1px solid rgba(148, 163, 184, 0.3)',
                background: '#0f172a',
                color: '#f8fafc',
                fontSize: 12,
              }}
            />
            <button
              type="button"
              disabled={busy || !dtmfDigits.trim()}
              onClick={() => {
                const digits = dtmfDigits.trim();
                setDtmfDigits('');
                void onSendEvent(activeSession.id, {
                  event: 'dtmf',
                  dtmf_digits: digits,
                });
              }}
              style={{
                padding: '6px 12px',
                borderRadius: 8,
                border: '1px solid rgba(148, 163, 184, 0.3)',
                background: 'rgba(30, 41, 59, 0.9)',
                color: '#cbd5e1',
                fontSize: 12,
                cursor: 'pointer',
              }}
            >
              Send DTMF
            </button>
          </div>
        </div>
      )}

      {activeSession && (
        <div
          data-testid="web-call-session-status"
          style={{
            marginTop: 12,
            padding: 10,
            borderRadius: 8,
            background: 'rgba(30, 41, 59, 0.6)',
            fontSize: 12,
            color: '#cbd5e1',
          }}
        >
          Session ID: <code>{activeSession.id}</code> · Status:{' '}
          <strong>{activeSession.status.toUpperCase()}</strong> · WebRTC State:{' '}
          <strong>
            {String(activeSession.final_output?.webrtc_state || 'IDLE')}
          </strong>
          {activeSession.error_code && (
            <div style={{ color: '#fca5a5', marginTop: 4 }}>
              {activeSession.error_code}: {activeSession.error_message}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default WebCallTester;
