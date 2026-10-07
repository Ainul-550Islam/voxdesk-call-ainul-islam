/**
 * dashboard/src/components/telephony/CallControlPanel.tsx
 * Live Call Session inspection and control panel:
 * State transitions, DTMF keypad, Cold/Warm/Agent transfer, barge-in,
 * transcript turns, and runtime event timeline.
 */

import React, { useState } from 'react';
import {
  CallSessionRecord,
  CallTransferPayload,
  TransferFallbackAction,
  TransferMode,
} from '../../lib/telephonyApi';
import { AgentSummaryOption } from './PhoneNumberTable';

export interface CallControlPanelProps {
  call: CallSessionRecord | null;
  agents: AgentSummaryOption[];
  onHangup: (callId: string, reason?: string) => Promise<void>;
  onSendDtmf: (callId: string, digits: string) => Promise<void>;
  onTransfer: (callId: string, payload: CallTransferPayload) => Promise<void>;
  onSendMediaEvent?: (
    callId: string,
    message: Record<string, unknown>
  ) => Promise<void>;
}

const TERMINAL_STATES = new Set([
  'COMPLETED',
  'FAILED',
  'CANCELLED',
  'BUSY',
  'NO_ANSWER',
  'VOICEMAIL',
]);

const KEYPAD_DIGITS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '*', '0', '#'];

export const CallControlPanel: React.FC<CallControlPanelProps> = ({
  call,
  agents,
  onHangup,
  onSendDtmf,
  onTransfer,
  onSendMediaEvent,
}) => {
  const [transferMode, setTransferMode] = useState<TransferMode>('COLD');
  const [transferTarget, setTransferTarget] = useState('+14155550188');
  const [targetAgentId, setTargetAgentId] = useState('');
  const [whisperMessage, setWhisperMessage] = useState(
    'Caller verified identity and requested billing assistance.'
  );
  const [fallbackAction, setFallbackAction] =
    useState<TransferFallbackAction>('RETURN_TO_AGENT');
  const [utteranceText, setUtteranceText] = useState('');
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [statusFeedback, setStatusFeedback] = useState<string | null>(null);

  if (!call) {
    return (
      <div
        data-testid="call-control-empty"
        style={{
          padding: '36px 24px',
          background: '#0f172a',
          borderRadius: '12px',
          border: '1px solid #1e293b',
          textAlign: 'center',
          color: '#94a3b8',
        }}
      >
        Select an active or historical call session to inspect runtime state, send DTMF, or initiate a transfer.
      </div>
    );
  }

  const isTerminal = TERMINAL_STATES.has((call.status || '').toUpperCase());

  const runAction = async (label: string, fn: () => Promise<void>) => {
    setBusyAction(label);
    setErrorMsg(null);
    setStatusFeedback(null);
    try {
      await fn();
      setStatusFeedback(`Action "${label}" completed.`);
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : `Action "${label}" failed.`);
    } finally {
      setBusyAction(null);
    }
  };

  return (
    <div
      data-testid="call-control-panel"
      style={{
        background: '#0f172a',
        borderRadius: '12px',
        border: '1px solid #1e293b',
        padding: '20px',
        color: '#f8fafc',
      }}
    >
      {/* Header */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          flexWrap: 'wrap',
          gap: '12px',
          borderBottom: '1px solid #1e293b',
          paddingBottom: '16px',
          marginBottom: '16px',
        }}
      >
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span
              style={{
                fontSize: '16px',
                fontWeight: 700,
                fontFamily: 'monospace',
              }}
            >
              {call.from_number} → {call.to_number}
            </span>
            <span
              data-testid="call-session-status-badge"
              style={{
                padding: '3px 10px',
                borderRadius: '999px',
                fontSize: '11px',
                fontWeight: 700,
                background: isTerminal
                  ? 'rgba(148, 163, 184, 0.18)'
                  : 'rgba(16, 185, 129, 0.18)',
                color: isTerminal ? '#cbd5e1' : '#34d399',
              }}
            >
              {call.status}
            </span>
            <span
              style={{
                padding: '3px 8px',
                borderRadius: '6px',
                fontSize: '11px',
                background: '#1e293b',
                color: '#93c5fd',
              }}
            >
              Media: {call.media_state}
            </span>
          </div>
          <div style={{ fontSize: '12px', color: '#94a3b8', marginTop: '6px' }}>
            Session ID: <code>{call.id}</code> · Provider: <strong>{call.provider}</strong> ({call.provider_call_id}) · Direction: <strong>{call.direction}</strong>
            {call.agent_id && (
              <>
                {' '}· Bound Agent: <code>{call.agent_id}</code>
              </>
            )}
          </div>
          <div style={{ fontSize: '12px', color: '#94a3b8', marginTop: '4px' }}>
            Duration: <strong>{(call.duration_ms / 1000).toFixed(1)}s</strong> · Billable: <strong>{call.billable_seconds}s</strong> · Mode: <strong>{call.execution_kind}</strong>
            {call.dtmf_buffer && (
              <>
                {' '}· DTMF Buffer: <code style={{ color: '#fbbf24' }}>{call.dtmf_buffer}</code>
              </>
            )}
          </div>
        </div>

        {!isTerminal && (
          <button
            type="button"
            data-testid="hangup-call-btn"
            disabled={busyAction !== null}
            onClick={() => runAction('Hangup Call', () => onHangup(call.id))}
            style={{
              padding: '8px 16px',
              borderRadius: '8px',
              border: '1px solid rgba(239, 68, 68, 0.5)',
              background: '#dc2626',
              color: '#ffffff',
              fontWeight: 700,
              fontSize: '13px',
              cursor: busyAction ? 'not-allowed' : 'pointer',
            }}
          >
            End / Hangup Call
          </button>
        )}
      </div>

      {errorMsg && (
        <div
          role="alert"
          style={{
            marginBottom: '14px',
            padding: '10px 14px',
            borderRadius: '8px',
            background: 'rgba(239, 68, 68, 0.14)',
            border: '1px solid rgba(239, 68, 68, 0.35)',
            color: '#f87171',
            fontSize: '13px',
          }}
        >
          {errorMsg}
        </div>
      )}

      {statusFeedback && (
        <div
          style={{
            marginBottom: '14px',
            padding: '10px 14px',
            borderRadius: '8px',
            background: 'rgba(16, 185, 129, 0.14)',
            border: '1px solid rgba(16, 185, 129, 0.35)',
            color: '#34d399',
            fontSize: '13px',
          }}
        >
          {statusFeedback}
        </div>
      )}

      {/* Active Call Controls: DTMF + Transfer + Realtime Media */}
      {!isTerminal && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
            gap: '16px',
            marginBottom: '20px',
          }}
        >
          {/* DTMF Keypad */}
          <div
            style={{
              background: '#020617',
              border: '1px solid #1e293b',
              borderRadius: '10px',
              padding: '14px',
            }}
          >
            <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '10px' }}>
              DTMF Keypad & IVR Navigation
            </div>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(3, 1fr)',
                gap: '8px',
              }}
            >
              {KEYPAD_DIGITS.map((digit) => (
                <button
                  key={digit}
                  type="button"
                  data-testid={`dtmf-key-${digit}`}
                  disabled={busyAction !== null}
                  onClick={() =>
                    runAction(`DTMF ${digit}`, () => onSendDtmf(call.id, digit))
                  }
                  style={{
                    padding: '8px 0',
                    borderRadius: '6px',
                    border: '1px solid #334155',
                    background: '#0f172a',
                    color: '#f8fafc',
                    fontSize: '14px',
                    fontWeight: 700,
                    fontFamily: 'monospace',
                    cursor: 'pointer',
                  }}
                >
                  {digit}
                </button>
              ))}
            </div>
          </div>

          {/* Transfer Orchestration */}
          <div
            style={{
              background: '#020617',
              border: '1px solid #1e293b',
              borderRadius: '10px',
              padding: '14px',
              display: 'flex',
              flexDirection: 'column',
              gap: '8px',
            }}
          >
            <div style={{ fontSize: '13px', fontWeight: 600 }}>
              Call Transfer (Cold / Warm / Agent Handoff)
            </div>
            <select
              aria-label="Transfer Mode"
              value={transferMode}
              onChange={(e) => setTransferMode(e.target.value as TransferMode)}
              style={{
                padding: '7px 10px',
                borderRadius: '6px',
                border: '1px solid #334155',
                background: '#0f172a',
                color: '#f8fafc',
                fontSize: '12px',
              }}
            >
              <option value="COLD">Cold Transfer (PSTN / SIP)</option>
              <option value="WARM">Warm Transfer (With Whisper Summary)</option>
              <option value="AGENT_TO_AGENT">Agent-to-Agent Handoff</option>
            </select>

            {transferMode === 'AGENT_TO_AGENT' ? (
              <select
                aria-label="Target Agent"
                value={targetAgentId}
                onChange={(e) => setTargetAgentId(e.target.value)}
                style={{
                  padding: '7px 10px',
                  borderRadius: '6px',
                  border: '1px solid #334155',
                  background: '#0f172a',
                  color: '#f8fafc',
                  fontSize: '12px',
                }}
              >
                <option value="">-- Select Target Agent --</option>
                {agents.map((ag) => (
                  <option key={ag.id} value={ag.id}>
                    {ag.name} ({ag.id.slice(0, 8)})
                  </option>
                ))}
              </select>
            ) : (
              <input
                type="text"
                aria-label="Transfer Destination"
                value={transferTarget}
                onChange={(e) => setTransferTarget(e.target.value)}
                placeholder="+14155550188 or sip:queue@carrier.example.com"
                style={{
                  padding: '7px 10px',
                  borderRadius: '6px',
                  border: '1px solid #334155',
                  background: '#0f172a',
                  color: '#f8fafc',
                  fontSize: '12px',
                }}
              />
            )}

            {transferMode === 'WARM' && (
              <input
                type="text"
                aria-label="Whisper Message"
                value={whisperMessage}
                onChange={(e) => setWhisperMessage(e.target.value)}
                placeholder="Whisper context summary for receiving agent..."
                style={{
                  padding: '7px 10px',
                  borderRadius: '6px',
                  border: '1px solid #334155',
                  background: '#0f172a',
                  color: '#f8fafc',
                  fontSize: '12px',
                }}
              />
            )}

            <select
              aria-label="Transfer Fallback Policy"
              value={fallbackAction}
              onChange={(e) =>
                setFallbackAction(e.target.value as TransferFallbackAction)
              }
              style={{
                padding: '7px 10px',
                borderRadius: '6px',
                border: '1px solid #334155',
                background: '#0f172a',
                color: '#f8fafc',
                fontSize: '12px',
              }}
            >
              <option value="RETURN_TO_AGENT">Fallback: Return Caller to Agent</option>
              <option value="RETRY">Fallback: Retry Transfer</option>
              <option value="HANGUP">Fallback: Polite Hangup</option>
            </select>

            <button
              type="button"
              data-testid="execute-transfer-btn"
              disabled={busyAction !== null}
              onClick={() =>
                runAction('Execute Transfer', () =>
                  onTransfer(call.id, {
                    mode: transferMode,
                    target_destination:
                      transferMode === 'AGENT_TO_AGENT'
                        ? targetAgentId || null
                        : transferTarget,
                    target_agent_id:
                      transferMode === 'AGENT_TO_AGENT'
                        ? targetAgentId || null
                        : null,
                    whisper_message:
                      transferMode === 'WARM' ? whisperMessage : null,
                    fallback_action: fallbackAction,
                  })
                )
              }
              style={{
                padding: '8px 12px',
                borderRadius: '6px',
                border: 'none',
                background: '#6366f1',
                color: '#ffffff',
                fontSize: '12px',
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              Initiate {transferMode} Transfer
            </button>
          </div>

          {/* Real-Time Media Gateway & Barge-In Controls */}
          {onSendMediaEvent && (
            <div
              style={{
                background: '#020617',
                border: '1px solid #1e293b',
                borderRadius: '10px',
                padding: '14px',
                display: 'flex',
                flexDirection: 'column',
                gap: '8px',
              }}
            >
              <div style={{ fontSize: '13px', fontWeight: 600 }}>
                Real-Time Media Stream & Barge-In
              </div>
              <div style={{ display: 'flex', gap: '8px' }}>
                <button
                  type="button"
                  onClick={() =>
                    runAction('Start Media Stream', () =>
                      onSendMediaEvent(call.id, {
                        type: 'media.start',
                        encoding: 'mulaw',
                        sample_rate: 8000,
                      })
                    )
                  }
                  style={{
                    flex: 1,
                    padding: '7px 10px',
                    borderRadius: '6px',
                    border: '1px solid #334155',
                    background: '#1e293b',
                    color: '#e2e8f0',
                    fontSize: '12px',
                    cursor: 'pointer',
                  }}
                >
                  Connect Audio
                </button>
                <button
                  type="button"
                  onClick={() =>
                    runAction('Trigger Barge-In', () =>
                      onSendMediaEvent(call.id, {
                        type: 'media.barge_in',
                        reason: 'operator_barge_in',
                      })
                    )
                  }
                  style={{
                    flex: 1,
                    padding: '7px 10px',
                    borderRadius: '6px',
                    border: '1px solid rgba(245, 158, 11, 0.4)',
                    background: 'rgba(245, 158, 11, 0.14)',
                    color: '#fbbf24',
                    fontSize: '12px',
                    cursor: 'pointer',
                  }}
                >
                  Barge-In / Interrupt
                </button>
              </div>
              <div style={{ display: 'flex', gap: '6px', marginTop: '4px' }}>
                <input
                  type="text"
                  value={utteranceText}
                  onChange={(e) => setUtteranceText(e.target.value)}
                  placeholder="Simulate caller utterance..."
                  style={{
                    flex: 1,
                    padding: '7px 10px',
                    borderRadius: '6px',
                    border: '1px solid #334155',
                    background: '#0f172a',
                    color: '#f8fafc',
                    fontSize: '12px',
                  }}
                />
                <button
                  type="button"
                  onClick={() => {
                    if (!utteranceText.trim()) return;
                    const text = utteranceText.trim();
                    setUtteranceText('');
                    runAction('Send Utterance', () =>
                      onSendMediaEvent(call.id, {
                        type: 'media.utterance',
                        text,
                      })
                    );
                  }}
                  style={{
                    padding: '7px 12px',
                    borderRadius: '6px',
                    border: 'none',
                    background: '#3b82f6',
                    color: '#ffffff',
                    fontSize: '12px',
                    fontWeight: 600,
                    cursor: 'pointer',
                  }}
                >
                  Speak
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Transcript & Runtime Events */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
          gap: '16px',
        }}
      >
        <div
          style={{
            background: '#020617',
            borderRadius: '10px',
            border: '1px solid #1e293b',
            padding: '14px',
          }}
        >
          <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '10px' }}>
            Live Call Transcript ({call.transcript_turns.length} turns)
          </div>
          {call.transcript_turns.length === 0 ? (
            <div style={{ fontSize: '12px', color: '#64748b' }}>
              No transcript turns recorded yet.
            </div>
          ) : (
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: '8px',
                maxHeight: '240px',
                overflowY: 'auto',
              }}
            >
              {call.transcript_turns.map((turn) => (
                <div
                  key={`${turn.turn_index}-${turn.timestamp}`}
                  style={{
                    padding: '8px 10px',
                    borderRadius: '6px',
                    background: '#0f172a',
                    border: '1px solid #1e293b',
                    fontSize: '12px',
                  }}
                >
                  <span
                    style={{
                      fontWeight: 700,
                      textTransform: 'uppercase',
                      color:
                        turn.role === 'agent'
                          ? '#60a5fa'
                          : turn.role === 'caller'
                          ? '#34d399'
                          : '#fbbf24',
                      marginRight: '8px',
                    }}
                  >
                    {turn.role}:
                  </span>
                  <span style={{ color: '#e2e8f0' }}>{turn.content}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div
          style={{
            background: '#020617',
            borderRadius: '10px',
            border: '1px solid #1e293b',
            padding: '14px',
          }}
        >
          <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '10px' }}>
            Runtime State Machine Timeline ({call.runtime_events.length} events)
          </div>
          {call.runtime_events.length === 0 ? (
            <div style={{ fontSize: '12px', color: '#64748b' }}>
              No runtime events recorded.
            </div>
          ) : (
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: '6px',
                maxHeight: '240px',
                overflowY: 'auto',
              }}
            >
              {call.runtime_events.map((evt) => (
                <div
                  key={evt.event_id}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    padding: '6px 10px',
                    borderRadius: '6px',
                    background: '#0f172a',
                    border: '1px solid #1e293b',
                    fontSize: '12px',
                  }}
                >
                  <div>
                    <span style={{ fontWeight: 700, color: '#a5b4fc' }}>
                      {evt.event_type}
                    </span>
                    <span style={{ marginLeft: '8px', color: '#94a3b8' }}>
                      state={evt.state}
                    </span>
                  </div>
                  <span style={{ fontSize: '11px', color: '#64748b' }}>
                    {new Date(evt.timestamp).toLocaleTimeString()}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default CallControlPanel;
