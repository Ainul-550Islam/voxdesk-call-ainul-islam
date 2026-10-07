import React, { useState } from 'react';
import type {
  CallTestReadiness,
  PhoneCallTestPayload,
  TestRun,
} from '../../types/evaluation';

export interface PhoneCallTesterProps {
  agentId: string;
  agentVersionNumber: number;
  readiness: CallTestReadiness | null;
  lastPhoneCall: TestRun | null;
  busy?: boolean;
  onRunPhoneCall: (payload: PhoneCallTestPayload) => Promise<TestRun | null>;
}

export const PhoneCallTester: React.FC<PhoneCallTesterProps> = ({
  agentId,
  agentVersionNumber,
  readiness,
  lastPhoneCall,
  busy = false,
  onRunPhoneCall,
}) => {
  const [toNumber, setToNumber] = useState('+14155550100');
  const [fromNumber, setFromNumber] = useState('+18005550199');
  const [scriptedTurnsText, setScriptedTurnsText] = useState('');
  const [requireLiveCarrier, setRequireLiveCarrier] = useState(true);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const turns = scriptedTurnsText
      .split('\n')
      .map((l) => l.trim())
      .filter(Boolean);
    await onRunPhoneCall({
      agent_id: agentId,
      agent_version_number: agentVersionNumber,
      to_number: toNumber.trim(),
      from_number: fromNumber.trim() || undefined,
      scripted_user_turns: turns,
      require_live_carrier: requireLiveCarrier,
    });
  };

  const carrierConfigured = Boolean(readiness?.telephony_carrier_configured);

  return (
    <div
      data-testid="phone-call-tester"
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
            Outbound Phone Call Tester
          </h3>
          <p style={{ margin: '4px 0 0', fontSize: 12, color: '#94a3b8' }}>
            Pinned to Agent <code>{agentId}</code> · <strong>v{agentVersionNumber}</strong>
          </p>
        </div>

        <span
          data-testid="carrier-readiness-badge"
          style={{
            padding: '4px 10px',
            borderRadius: 999,
            fontSize: 11,
            fontWeight: 700,
            background: carrierConfigured
              ? 'rgba(16, 185, 129, 0.16)'
              : 'rgba(239, 68, 68, 0.16)',
            color: carrierConfigured ? '#34d399' : '#fca5a5',
          }}
        >
          {carrierConfigured
            ? 'PSTN CARRIER CONFIGURED'
            : 'CARRIER UNCONFIGURED (REPORTS HONEST NOT_RUN / ERROR)'}
        </span>
      </div>

      <form
        onSubmit={handleSubmit}
        data-testid="phone-call-test-form"
        style={{ display: 'flex', flexDirection: 'column', gap: 10 }}
      >
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
            gap: 10,
          }}
        >
          <div>
            <label
              style={{
                display: 'block',
                fontSize: 11,
                color: '#94a3b8',
                marginBottom: 4,
              }}
            >
              Destination Phone Number (E.164)
            </label>
            <input
              type="text"
              aria-label="Destination Phone Number"
              value={toNumber}
              onChange={(e) => setToNumber(e.target.value)}
              style={{
                width: '100%',
                padding: '8px 10px',
                borderRadius: 8,
                border: '1px solid rgba(148, 163, 184, 0.3)',
                background: '#0f172a',
                color: '#f8fafc',
                fontSize: 13,
              }}
            />
          </div>

          <div>
            <label
              style={{
                display: 'block',
                fontSize: 11,
                color: '#94a3b8',
                marginBottom: 4,
              }}
            >
              Caller ID / From Number
            </label>
            <input
              type="text"
              aria-label="From Phone Number"
              value={fromNumber}
              onChange={(e) => setFromNumber(e.target.value)}
              style={{
                width: '100%',
                padding: '8px 10px',
                borderRadius: 8,
                border: '1px solid rgba(148, 163, 184, 0.3)',
                background: '#0f172a',
                color: '#f8fafc',
                fontSize: 13,
              }}
            />
          </div>
        </div>

        <div>
          <label
            style={{
              display: 'block',
              fontSize: 11,
              color: '#94a3b8',
              marginBottom: 4,
            }}
          >
            Scripted Caller Turns (one per line — used when sandbox loopback is enabled)
          </label>
          <textarea
            rows={3}
            aria-label="Scripted Caller Turns"
            placeholder={
              'Hello, I need to book an appointment tomorrow at 10 AM\nThank you, goodbye'
            }
            value={scriptedTurnsText}
            onChange={(e) => setScriptedTurnsText(e.target.value)}
            style={{
              width: '100%',
              padding: '8px 10px',
              borderRadius: 8,
              border: '1px solid rgba(148, 163, 184, 0.3)',
              background: '#0f172a',
              color: '#f8fafc',
              fontSize: 12,
            }}
          />
        </div>

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
              aria-label="Require Live Telephony Carrier"
              checked={requireLiveCarrier}
              onChange={(e) => setRequireLiveCarrier(e.target.checked)}
            />
            Require live telephony carrier (never fake a PSTN dial when unconfigured)
          </label>

          <button
            type="submit"
            data-testid="run-phone-call-test-btn"
            disabled={busy || !toNumber.trim()}
            style={{
              padding: '8px 16px',
              borderRadius: 8,
              border: 'none',
              background: '#3b82f6',
              color: '#ffffff',
              fontWeight: 700,
              fontSize: 13,
              cursor: busy ? 'not-allowed' : 'pointer',
            }}
          >
            {busy ? 'Dialing / Running...' : 'Run Phone Call Test'}
          </button>
        </div>
      </form>

      {lastPhoneCall && (
        <div
          data-testid="phone-call-result-banner"
          style={{
            marginTop: 14,
            padding: 12,
            borderRadius: 8,
            background:
              lastPhoneCall.status === 'passed'
                ? 'rgba(16, 185, 129, 0.14)'
                : lastPhoneCall.status === 'not_run' ||
                  lastPhoneCall.status === 'error'
                ? 'rgba(245, 158, 11, 0.16)'
                : 'rgba(239, 68, 68, 0.16)',
            border: '1px solid rgba(148, 163, 184, 0.25)',
            fontSize: 12,
            color: '#e2e8f0',
          }}
        >
          <div>
            Phone Test Run <code>{lastPhoneCall.id}</code> · Status:{' '}
            <strong data-testid="phone-call-run-status">
              {lastPhoneCall.status.toUpperCase()}
            </strong>{' '}
            · Pinned v{lastPhoneCall.agent_version_number}
          </div>
          {lastPhoneCall.error_code && (
            <div
              data-testid="phone-call-error-code"
              style={{ marginTop: 4, color: '#fcd34d' }}
            >
              <strong>{lastPhoneCall.error_code}:</strong>{' '}
              {lastPhoneCall.error_message}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default PhoneCallTester;
