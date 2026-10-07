/**
 * dashboard/src/pages/PhoneNumbersPage.tsx
 * Connected operator console page for E.164 Phone Number Lifecycle Management,
 * Inbound/Outbound Agent Bindings, SIP Trunking, and Provider Readiness.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { listAgents } from '../api/agents';
import {
  bindPhoneNumberAgent,
  createPhoneNumber,
  createSipConnection,
  deletePhoneNumber,
  getTelephonyHealth,
  listPhoneNumbers,
  listSipConnections,
  PhoneNumberBindAgentPayload,
  PhoneNumberRecord,
  SipConnectionCreatePayload,
  SipConnectionRecord,
  TelephonyHealthStatus,
  TelephonyProviderName,
  testSipConnection,
} from '../lib/telephonyApi';
import {
  AgentSummaryOption,
  PhoneNumberTable,
} from '../components/telephony/PhoneNumberTable';
import { SipConnectionDialog } from '../components/telephony/SipConnectionDialog';

export const PhoneNumbersPage: React.FC = () => {
  const [numbers, setNumbers] = useState<PhoneNumberRecord[]>([]);
  const [sipConnections, setSipConnections] = useState<SipConnectionRecord[]>([]);
  const [agents, setAgents] = useState<AgentSummaryOption[]>([]);
  const [health, setHealth] = useState<TelephonyHealthStatus | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // New phone number form state
  const [newNumber, setNewNumber] = useState('');
  const [provider, setProvider] = useState<TelephonyProviderName>('TWILIO');
  const [inboundAgentId, setInboundAgentId] = useState('');
  const [outboundAgentId, setOutboundAgentId] = useState('');
  const [sipEnabled, setSipEnabled] = useState(false);
  const [selectedSipId, setSelectedSipId] = useState('');
  const [creating, setCreating] = useState(false);

  const refreshData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [numRes, sipRes, healthRes, agentRes] = await Promise.all([
        listPhoneNumbers(),
        listSipConnections(),
        getTelephonyHealth().catch(() => null),
        listAgents().catch(() => ({ agents: [], total: 0 })),
      ]);
      setNumbers(numRes.items || []);
      setSipConnections(sipRes.items || []);
      if (healthRes) {
        setHealth(healthRes);
      }
      const agentOptions: AgentSummaryOption[] = (agentRes.agents || []).map(
        (ag) => ({
          id: ag.id,
          name: ag.name,
          status: ag.status,
        })
      );
      setAgents(agentOptions);
    } catch (err: unknown) {
      setError(
        err instanceof Error
          ? err.message
          : 'Failed to load telephony configuration.'
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshData();
  }, [refreshData]);

  const handleCreateNumber = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreating(true);
    setError(null);
    try {
      const created = await createPhoneNumber({
        number: newNumber.trim(),
        provider,
        inbound_agent_id: inboundAgentId || null,
        outbound_agent_id: outboundAgentId || null,
        sip_enabled: sipEnabled,
        sip_connection_id: sipEnabled && selectedSipId ? selectedSipId : null,
      });
      setNumbers((prev) => [created, ...prev]);
      const healthRes = await getTelephonyHealth().catch(() => null);
      if (healthRes) setHealth(healthRes);
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : 'Failed to register phone number.'
      );
    } finally {
      setCreating(false);
    }
  };

  const handleBindAgent = async (
    phoneNumberId: string,
    payload: PhoneNumberBindAgentPayload
  ) => {
    const updated = await bindPhoneNumberAgent(phoneNumberId, payload);
    setNumbers((prev) =>
      prev.map((item) => (item.id === phoneNumberId ? updated : item))
    );
  };

  const handleDeleteNumber = async (phoneNumberId: string) => {
    await deletePhoneNumber(phoneNumberId);
    setNumbers((prev) => prev.filter((item) => item.id !== phoneNumberId));
  };

  const handleCreateSip = async (
    payload: SipConnectionCreatePayload
  ): Promise<SipConnectionRecord> => {
    const created = await createSipConnection(payload);
    setSipConnections((prev) => [created, ...prev]);
    return created;
  };

  const handleTestSip = async (
    sipConnectionId: string
  ): Promise<SipConnectionRecord> => {
    const updated = await testSipConnection(sipConnectionId);
    setSipConnections((prev) =>
      prev.map((c) => (c.id === sipConnectionId ? updated : c))
    );
    return updated;
  };

  return (
    <div
      data-testid="phone-numbers-page"
      style={{
        minHeight: '100vh',
        background: '#020617',
        color: '#f8fafc',
        padding: '28px 32px',
        fontFamily: 'Inter, system-ui, sans-serif',
      }}
    >
      <div style={{ maxWidth: '1280px', margin: '0 auto' }}>
        {/* Page Header */}
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
              Phone Numbers & SIP Trunking
            </h1>
            <p style={{ margin: '6px 0 0', fontSize: '14px', color: '#94a3b8' }}>
              Provision E.164 phone numbers, bind inbound and outbound voice agents, and verify RFC 3261 SIP trunks.
            </p>
          </div>

          {health && (
            <div
              data-testid="telephony-health-badge"
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '12px',
                padding: '10px 16px',
                borderRadius: '10px',
                background: '#0f172a',
                border: '1px solid #1e293b',
                fontSize: '13px',
              }}
            >
              <span>
                Runtime State:{' '}
                <strong
                  style={{
                    color:
                      health.state === 'READY'
                        ? '#34d399'
                        : health.state === 'CONFIGURED'
                        ? '#60a5fa'
                        : '#fbbf24',
                  }}
                >
                  {health.state}
                </strong>
              </span>
              <span style={{ color: '#64748b' }}>|</span>
              <span>
                Ready Numbers:{' '}
                <strong>
                  {health.ready_phone_numbers}/{health.configured_phone_numbers}
                </strong>
              </span>
              <span style={{ color: '#64748b' }}>|</span>
              <span>
                Ready SIP:{' '}
                <strong>
                  {health.ready_sip_connections}/
                  {health.configured_sip_connections}
                </strong>
              </span>
            </div>
          )}
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

        {/* Register / Provision E.164 Number Card */}
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
            Provision or Attach E.164 Phone Number
          </h2>
          <form
            onSubmit={handleCreateNumber}
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(195px, 1fr))',
              gap: '12px',
              alignItems: 'end',
            }}
          >
            <div>
              <label
                htmlFor="e164-number-input"
                style={{
                  display: 'block',
                  fontSize: '12px',
                  color: '#94a3b8',
                  marginBottom: '4px',
                }}
              >
                E.164 Phone Number
              </label>
              <input
                id="e164-number-input"
                type="text"
                value={newNumber}
                onChange={(e) => setNewNumber(e.target.value)}
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

            <div>
              <label
                htmlFor="provider-select"
                style={{
                  display: 'block',
                  fontSize: '12px',
                  color: '#94a3b8',
                  marginBottom: '4px',
                }}
              >
                Carrier / Provider
              </label>
              <select
                id="provider-select"
                value={provider}
                onChange={(e) =>
                  setProvider(e.target.value as TelephonyProviderName)
                }
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
                <option value="TWILIO">Twilio</option>
                <option value="TELNYX">Telnyx</option>
                <option value="VONAGE">Vonage</option>
                <option value="SIP">BYOC SIP Trunk</option>
                <option value="SIMULATED">Simulated Sandbox</option>
              </select>
            </div>

            <div>
              <label
                htmlFor="new-inbound-agent"
                style={{
                  display: 'block',
                  fontSize: '12px',
                  color: '#94a3b8',
                  marginBottom: '4px',
                }}
              >
                Inbound Agent Binding
              </label>
              <select
                id="new-inbound-agent"
                value={inboundAgentId}
                onChange={(e) => setInboundAgentId(e.target.value)}
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
                <option value="">-- Unbound --</option>
                {agents.map((ag) => (
                  <option key={ag.id} value={ag.id}>
                    {ag.name}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label
                htmlFor="new-outbound-agent"
                style={{
                  display: 'block',
                  fontSize: '12px',
                  color: '#94a3b8',
                  marginBottom: '4px',
                }}
              >
                Outbound Agent Binding
              </label>
              <select
                id="new-outbound-agent"
                value={outboundAgentId}
                onChange={(e) => setOutboundAgentId(e.target.value)}
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
                <option value="">-- Unbound --</option>
                {agents.map((ag) => (
                  <option key={ag.id} value={ag.id}>
                    {ag.name}
                  </option>
                ))}
              </select>
            </div>

            {sipConnections.length > 0 && (
              <div>
                <label
                  htmlFor="new-sip-connection"
                  style={{
                    display: 'block',
                    fontSize: '12px',
                    color: '#94a3b8',
                    marginBottom: '4px',
                  }}
                >
                  SIP Connection (Optional)
                </label>
                <select
                  id="new-sip-connection"
                  value={selectedSipId}
                  onChange={(e) => {
                    setSelectedSipId(e.target.value);
                    setSipEnabled(Boolean(e.target.value));
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
                  <option value="">None (PSTN Carrier)</option>
                  {sipConnections.map((sc) => (
                    <option key={sc.id} value={sc.id}>
                      {sc.name} ({sc.status})
                    </option>
                  ))}
                </select>
              </div>
            )}

            <div>
              <button
                type="submit"
                disabled={creating}
                style={{
                  width: '100%',
                  padding: '9px 16px',
                  borderRadius: '6px',
                  border: 'none',
                  background: '#3b82f6',
                  color: '#ffffff',
                  fontSize: '13px',
                  fontWeight: 600,
                  cursor: creating ? 'not-allowed' : 'pointer',
                }}
              >
                {creating ? 'Provisioning...' : 'Attach Number'}
              </button>
            </div>
          </form>
        </div>

        {/* Phone Number Table */}
        <div style={{ marginBottom: '24px' }}>
          <PhoneNumberTable
            numbers={numbers}
            agents={agents}
            loading={loading}
            onBindAgent={handleBindAgent}
            onDeleteNumber={handleDeleteNumber}
          />
        </div>

        {/* SIP Trunking Configuration */}
        <SipConnectionDialog
          connections={sipConnections}
          onCreateConnection={handleCreateSip}
          onTestConnection={handleTestSip}
        />
      </div>
    </div>
  );
};

export default PhoneNumbersPage;
