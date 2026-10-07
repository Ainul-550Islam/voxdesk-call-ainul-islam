/**
 * dashboard/src/pages/CallRuntimePage.tsx
 * Connected operator console page for Outbound Call Origination,
 * Live Call Session State Machine inspection, DTMF / IVR, Transfer,
 * Real-Time Media Gateway, and Authoritative Usage Ledger Summary.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { listAgents } from '../api/agents';
import {
  CallSessionRecord,
  CallTransferPayload,
  createOutboundCall,
  getTelephonyCall,
  getTelephonyHealth,
  getTelephonyUsage,
  hangupTelephonyCall,
  listPhoneNumbers,
  listTelephonyCalls,
  PhoneNumberRecord,
  postTelephonyCallMediaEvent,
  sendTelephonyCallDtmf,
  TelephonyHealthStatus,
  TelephonyProviderName,
  TelephonyUsageSummary,
  transferTelephonyCall,
} from '../lib/telephonyApi';
import { CallControlPanel } from '../components/telephony/CallControlPanel';
import { AgentSummaryOption } from '../components/telephony/PhoneNumberTable';

export const CallRuntimePage: React.FC = () => {
  const [calls, setCalls] = useState<CallSessionRecord[]>([]);
  const [selectedCallId, setSelectedCallId] = useState<string | null>(null);
  const [numbers, setNumbers] = useState<PhoneNumberRecord[]>([]);
  const [agents, setAgents] = useState<AgentSummaryOption[]>([]);
  const [health, setHealth] = useState<TelephonyHealthStatus | null>(null);
  const [usage, setUsage] = useState<TelephonyUsageSummary | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const outboundIntent = useRef<{ signature: string; key: string } | null>(null);

  // Outbound dialer state
  const [toNumber, setToNumber] = useState('');
  const [selectedPhoneId, setSelectedPhoneId] = useState('');
  const [fromNumber, setFromNumber] = useState('');
  const [selectedAgentId, setSelectedAgentId] = useState('');
  const [provider, setProvider] = useState<TelephonyProviderName>('SIMULATED');
  const [isSimulation, setIsSimulation] = useState(true);
  const [dialing, setDialing] = useState(false);

  const refreshAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [callRes, numRes, agentRes, healthRes, usageRes] =
        await Promise.all([
          listTelephonyCalls({ limit: 50 }),
          listPhoneNumbers().catch(() => ({ items: [], total: 0 })),
          listAgents().catch(() => ({ agents: [], total: 0 })),
          getTelephonyHealth().catch(() => null),
          getTelephonyUsage(true).catch(() => null),
        ]);
      const callItems = callRes.items || [];
      setCalls(callItems);
      setNumbers(numRes.items || []);
      if (numRes.items && numRes.items.length > 0 && !selectedPhoneId) {
        setSelectedPhoneId(numRes.items[0].id);
        setFromNumber(numRes.items[0].e164_number);
      }
      const agentOptions: AgentSummaryOption[] = (agentRes.agents || []).map(
        (ag) => ({
          id: ag.id,
          name: ag.name,
          status: ag.status,
        })
      );
      setAgents(agentOptions);
      if (agentOptions.length > 0 && !selectedAgentId) {
        setSelectedAgentId(agentOptions[0].id);
      }
      if (healthRes) setHealth(healthRes);
      if (usageRes) setUsage(usageRes);
      if (callItems.length > 0 && !selectedCallId) {
        setSelectedCallId(callItems[0].id);
      }
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : 'Failed to load call runtime.'
      );
    } finally {
      setLoading(false);
    }
  }, [selectedAgentId, selectedCallId, selectedPhoneId]);

  useEffect(() => {
    void refreshAll();
  }, [refreshAll]);

  const reloadCall = async (callId: string) => {
    const fresh = await getTelephonyCall(callId);
    setCalls((prev) => prev.map((c) => (c.id === callId ? fresh : c)));
    const usageRes = await getTelephonyUsage(true).catch(() => null);
    if (usageRes) setUsage(usageRes);
  };

  const handlePlaceOutboundCall = async (e: React.FormEvent) => {
    e.preventDefault();
    setDialing(true);
    setError(null);
    try {
      const request = {
        to_number: toNumber.trim(),
        phone_number_id: selectedPhoneId || null,
        from_number: selectedPhoneId ? null : fromNumber.trim(),
        agent_id: selectedAgentId || null,
        provider,
        is_simulation: isSimulation,
      };
      const signature = JSON.stringify(request);
      if (!outboundIntent.current || outboundIntent.current.signature !== signature) {
        if (typeof globalThis.crypto?.randomUUID !== 'function') {
          throw new Error('Secure call idempotency keys are unavailable in this browser.');
        }
        outboundIntent.current = {
          signature,
          key: globalThis.crypto.randomUUID(),
        };
      }
      const created = await createOutboundCall({
        ...request,
        idempotency_key: outboundIntent.current.key,
      });
      outboundIntent.current = null;
      setCalls((prev) => [created, ...prev]);
      setSelectedCallId(created.id);
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : 'Failed to place outbound call.'
      );
    } finally {
      setDialing(false);
    }
  };

  const handleHangup = async (callId: string, reason = 'operator_hangup') => {
    const updated = await hangupTelephonyCall(callId, reason);
    setCalls((prev) => prev.map((c) => (c.id === callId ? updated : c)));
    const usageRes = await getTelephonyUsage(true).catch(() => null);
    if (usageRes) setUsage(usageRes);
  };

  const handleSendDtmf = async (callId: string, digits: string) => {
    await sendTelephonyCallDtmf(callId, { digits, source: 'operator_console' });
    await reloadCall(callId);
  };

  const handleTransfer = async (
    callId: string,
    payload: CallTransferPayload
  ) => {
    await transferTelephonyCall(callId, payload);
    await reloadCall(callId);
  };

  const handleMediaEvent = async (
    callId: string,
    message: Record<string, unknown>
  ) => {
    await postTelephonyCallMediaEvent(callId, message);
    await reloadCall(callId);
  };

  const selectedCall =
    calls.find((c) => c.id === selectedCallId) || calls[0] || null;

  return (
    <div
      data-testid="call-runtime-page"
      style={{
        minHeight: '100vh',
        background: '#020617',
        color: '#f8fafc',
        padding: '28px 32px',
        fontFamily: 'Inter, system-ui, sans-serif',
      }}
    >
      <div style={{ maxWidth: '1320px', margin: '0 auto' }}>
        {/* Header & Metrics */}
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            flexWrap: 'wrap',
            gap: '16px',
            marginBottom: '24px',
          }}
        >
          <div>
            <h1 style={{ margin: 0, fontSize: '24px', fontWeight: 700 }}>
              Voice / Telephony Call Runtime
            </h1>
            <p style={{ margin: '6px 0 0', fontSize: '14px', color: '#94a3b8' }}>
              Originate outbound calls, monitor real-time media gateway state, send DTMF, and execute Cold/Warm/Agent transfers.
            </p>
          </div>

          <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
            {health && (
              <div
                style={{
                  padding: '10px 14px',
                  borderRadius: '10px',
                  background: '#0f172a',
                  border: '1px solid #1e293b',
                  fontSize: '12px',
                }}
              >
                Active Calls: <strong>{health.active_calls}</strong> · Provider:{' '}
                <strong>{health.default_provider}</strong> ({health.state})
              </div>
            )}
            {usage && (
              <div
                data-testid="telephony-usage-summary"
                style={{
                  padding: '10px 14px',
                  borderRadius: '10px',
                  background: '#0f172a',
                  border: '1px solid #1e293b',
                  fontSize: '12px',
                }}
              >
                Finalized Calls: <strong>{usage.call_count}</strong> · Billable:{' '}
                <strong>{usage.total_billable_seconds}s</strong> (
                {usage.total_billable_minutes}m)
              </div>
            )}
          </div>
        </div>

        {error && (
          <div
            role="alert"
            style={{
              marginBottom: '18px',
              padding: '12px 16px',
              borderRadius: '10px',
              background: 'rgba(239, 68, 68, 0.14)',
              border: '1px solid rgba(239, 68, 68, 0.35)',
              color: '#f87171',
              fontSize: '13px',
            }}
          >
            {error}
          </div>
        )}

        {/* Outbound Call Dialer */}
        <div
          style={{
            background: '#0f172a',
            borderRadius: '12px',
            border: '1px solid #1e293b',
            padding: '20px',
            marginBottom: '24px',
          }}
        >
          <h2 style={{ margin: '0 0 14px', fontSize: '16px', fontWeight: 600 }}>
            Originate Outbound Call
          </h2>
          <form
            onSubmit={handlePlaceOutboundCall}
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))',
              gap: '12px',
              alignItems: 'end',
            }}
          >
            <div>
              <label
                htmlFor="outbound-to-number"
                style={{
                  display: 'block',
                  fontSize: '12px',
                  color: '#94a3b8',
                  marginBottom: '4px',
                }}
              >
                Destination (E.164)
              </label>
              <input
                id="outbound-to-number"
                type="text"
                value={toNumber}
                onChange={(e) => setToNumber(e.target.value)}
                placeholder="+[country code][subscriber number]"
                required
                style={{
                  width: '100%',
                  padding: '8px 10px',
                  borderRadius: '6px',
                  border: '1px solid #334155',
                  background: '#020617',
                  color: '#f8fafc',
                  fontSize: '13px',
                  fontFamily: 'monospace',
                }}
              />
            </div>

            {numbers.length > 0 ? (
              <div>
                <label
                  htmlFor="outbound-phone-select"
                  style={{
                    display: 'block',
                    fontSize: '12px',
                    color: '#94a3b8',
                    marginBottom: '4px',
                  }}
                >
                  Caller ID Number
                </label>
                <select
                  id="outbound-phone-select"
                  value={selectedPhoneId}
                  onChange={(e) => setSelectedPhoneId(e.target.value)}
                  style={{
                    width: '100%',
                    padding: '8px 10px',
                    borderRadius: '6px',
                    border: '1px solid #334155',
                    background: '#020617',
                    color: '#f8fafc',
                    fontSize: '13px',
                  }}
                >
                  {numbers.map((num) => (
                    <option key={num.id} value={num.id}>
                      {num.e164_number} ({num.provider})
                    </option>
                  ))}
                </select>
              </div>
            ) : (
              <div>
                <label
                  htmlFor="outbound-from-number"
                  style={{
                    display: 'block',
                    fontSize: '12px',
                    color: '#94a3b8',
                    marginBottom: '4px',
                  }}
                >
                  Caller ID (E.164)
                </label>
                <input
                  id="outbound-from-number"
                  type="text"
                  value={fromNumber}
                  onChange={(e) => setFromNumber(e.target.value)}
                  placeholder="+[country code][subscriber number]"
                  required
                  style={{
                    width: '100%',
                    padding: '8px 10px',
                    borderRadius: '6px',
                    border: '1px solid #334155',
                    background: '#020617',
                    color: '#f8fafc',
                    fontSize: '13px',
                    fontFamily: 'monospace',
                  }}
                />
              </div>
            )}

            <div>
              <label
                htmlFor="outbound-agent-select"
                style={{
                  display: 'block',
                  fontSize: '12px',
                  color: '#94a3b8',
                  marginBottom: '4px',
                }}
              >
                Voice Agent
              </label>
              <select
                id="outbound-agent-select"
                value={selectedAgentId}
                onChange={(e) => setSelectedAgentId(e.target.value)}
                required
                style={{
                  width: '100%',
                  padding: '8px 10px',
                  borderRadius: '6px',
                  border: '1px solid #334155',
                  background: '#020617',
                  color: '#f8fafc',
                  fontSize: '13px',
                }}
              >
                <option value="">Select a configured agent</option>
                {agents.map((ag) => (
                  <option key={ag.id} value={ag.id}>
                    {ag.name}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label
                htmlFor="outbound-provider-select"
                style={{
                  display: 'block',
                  fontSize: '12px',
                  color: '#94a3b8',
                  marginBottom: '4px',
                }}
              >
                Provider Mode
              </label>
              <select
                id="outbound-provider-select"
                value={provider}
                onChange={(e) => {
                  const val = e.target.value as TelephonyProviderName;
                  setProvider(val);
                  setIsSimulation(val === 'SIMULATED');
                }}
                style={{
                  width: '100%',
                  padding: '8px 10px',
                  borderRadius: '6px',
                  border: '1px solid #334155',
                  background: '#020617',
                  color: '#f8fafc',
                  fontSize: '13px',
                }}
              >
                <option value="SIMULATED">Simulated Runtime (Sandbox)</option>
                <option value="TWILIO">Twilio PSTN</option>
                <option value="TELNYX">Telnyx Call Control</option>
                <option value="VONAGE">Vonage Voice</option>
                <option value="SIP">BYOC SIP Trunk</option>
              </select>
            </div>

            <div>
              <button
                type="submit"
                data-testid="place-outbound-call-btn"
                disabled={dialing}
                style={{
                  width: '100%',
                  padding: '9px 16px',
                  borderRadius: '6px',
                  border: 'none',
                  background: '#10b981',
                  color: '#020617',
                  fontSize: '13px',
                  fontWeight: 700,
                  cursor: dialing ? 'not-allowed' : 'pointer',
                }}
              >
                {dialing ? 'Dialing...' : 'Place Outbound Call'}
              </button>
            </div>
          </form>
        </div>

        {/* Main Split Layout: Call Sessions List + CallControlPanel */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'minmax(280px, 360px) 1fr',
            gap: '20px',
            alignItems: 'start',
          }}
        >
          {/* Left: Call Sessions List */}
          <div
            style={{
              background: '#0f172a',
              borderRadius: '12px',
              border: '1px solid #1e293b',
              overflow: 'hidden',
            }}
          >
            <div
              style={{
                padding: '14px 16px',
                borderBottom: '1px solid #1e293b',
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <span style={{ fontSize: '14px', fontWeight: 600 }}>
                Call Sessions ({calls.length})
              </span>
              <button
                type="button"
                onClick={() => void refreshAll()}
                style={{
                  padding: '4px 10px',
                  borderRadius: '6px',
                  border: '1px solid #334155',
                  background: '#1e293b',
                  color: '#cbd5e1',
                  fontSize: '11px',
                  cursor: 'pointer',
                }}
              >
                Refresh
              </button>
            </div>

            {loading && calls.length === 0 ? (
              <div style={{ padding: '24px', color: '#94a3b8', fontSize: '13px' }}>
                Loading call sessions...
              </div>
            ) : calls.length === 0 ? (
              <div
                data-testid="call-list-empty"
                style={{ padding: '24px', color: '#94a3b8', fontSize: '13px' }}
              >
                No call sessions recorded yet. Place an outbound call above to start a session.
              </div>
            ) : (
              <div style={{ maxHeight: '620px', overflowY: 'auto' }}>
                {calls.map((c) => {
                  const selected = selectedCall?.id === c.id;
                  return (
                    <button
                      key={c.id}
                      type="button"
                      data-testid={`call-session-item-${c.id}`}
                      onClick={() => setSelectedCallId(c.id)}
                      style={{
                        width: '100%',
                        textAlign: 'left',
                        padding: '12px 16px',
                        border: 'none',
                        borderBottom: '1px solid #1e293b',
                        background: selected
                          ? 'rgba(59, 130, 246, 0.14)'
                          : 'transparent',
                        color: '#f8fafc',
                        cursor: 'pointer',
                      }}
                    >
                      <div
                        style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center',
                        }}
                      >
                        <span
                          style={{
                            fontFamily: 'monospace',
                            fontSize: '13px',
                            fontWeight: 600,
                          }}
                        >
                          {c.from_number} → {c.to_number}
                        </span>
                        <span
                          style={{
                            fontSize: '11px',
                            fontWeight: 700,
                            color: '#60a5fa',
                          }}
                        >
                          {c.status}
                        </span>
                      </div>
                      <div
                        style={{
                          fontSize: '11px',
                          color: '#94a3b8',
                          marginTop: '4px',
                        }}
                      >
                        {c.direction} · {c.provider} · {(c.duration_ms / 1000).toFixed(1)}s
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {/* Right: Active Call Control Panel */}
          <CallControlPanel
            call={selectedCall}
            agents={agents}
            onHangup={handleHangup}
            onSendDtmf={handleSendDtmf}
            onTransfer={handleTransfer}
            onSendMediaEvent={handleMediaEvent}
          />
        </div>
      </div>
    </div>
  );
};

export default CallRuntimePage;
