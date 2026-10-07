import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  getCapabilityInventory,
  getIntegrationInventory,
  inspectE2EFlow,
  type CapabilityInventory,
  type E2EInspection,
  type IntegrationInventory,
} from '../lib/parityApi';
import { CapabilityStatusCard } from '../components/parity/CapabilityStatusCard';
import { E2EFlowTimeline } from '../components/parity/E2EFlowTimeline';
import { RetellParityMatrix } from '../components/parity/RetellParityMatrix';

function describeError(error: unknown): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  return 'The request failed without a readable error message.';
}

function IntegrationStateCard({ item }: { item: IntegrationInventory['items'][number] }) {
  const color = item.status === 'CONNECTED'
    ? '#8fe0a7'
    : item.status === 'ERROR'
      ? '#ff9c9c'
      : item.status === 'UNVERIFIED' || item.status === 'SANDBOX'
        ? '#ffd47e'
        : '#b6c1d0';

  return (
    <article
      style={{
        minWidth: 0,
        background: '#111923',
        border: '1px solid #293544',
        borderRadius: 12,
        padding: 14,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
        <strong>{item.integration_type} · {item.provider}</strong>
        <span style={{ color, fontWeight: 700, fontSize: 12 }}>{item.status}</span>
      </div>
      <p style={{ color: '#b8c3d0', fontSize: 12, lineHeight: 1.5, margin: '9px 0' }}>
        {item.status_basis}
      </p>
      <div style={{ color: '#93a0b0', fontSize: 11 }}>
        Configured: {item.configured ? 'yes' : 'no'} · Enabled: {item.enabled === null ? 'not applicable' : item.enabled ? 'yes' : 'no'} · Credential material present: {item.credentials_present ? 'yes' : 'no'}
      </div>
      {item.last_health_check_at && (
        <div style={{ color: '#93a0b0', fontSize: 11, marginTop: 5 }}>
          Last persisted health check: {new Date(item.last_health_check_at).toLocaleString()} · result: {item.last_health_ok === true ? 'success' : item.last_health_ok === false ? 'failure' : 'unknown'}
        </div>
      )}
    </article>
  );
}

export function FinalParityPage() {
  const [capabilityData, setCapabilityData] = useState<CapabilityInventory | null>(null);
  const [integrationData, setIntegrationData] = useState<IntegrationInventory | null>(null);
  const [capabilityError, setCapabilityError] = useState<string | null>(null);
  const [integrationError, setIntegrationError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [agentId, setAgentId] = useState('');
  const [callId, setCallId] = useState('');
  const [inspection, setInspection] = useState<E2EInspection | null>(null);
  const [inspectionError, setInspectionError] = useState<string | null>(null);
  const [inspectionLoading, setInspectionLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setCapabilityError(null);
    setIntegrationError(null);
    const [capabilityResult, integrationResult] = await Promise.allSettled([
      getCapabilityInventory(),
      getIntegrationInventory(),
    ]);
    if (capabilityResult.status === 'fulfilled') {
      setCapabilityData(capabilityResult.value);
    } else {
      setCapabilityError(describeError(capabilityResult.reason));
    }
    if (integrationResult.status === 'fulfilled') {
      setIntegrationData(integrationResult.value);
    } else {
      setIntegrationError(describeError(integrationResult.reason));
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const statusCounts = useMemo(() => {
    const counts = { IMPLEMENTED: 0, PARTIAL: 0, MISSING: 0, other: 0 };
    for (const item of capabilityData?.capabilities ?? []) {
      if (item.status === 'IMPLEMENTED') counts.IMPLEMENTED += 1;
      else if (item.status === 'PARTIAL') counts.PARTIAL += 1;
      else if (item.status === 'MISSING') counts.MISSING += 1;
      else counts.other += 1;
    }
    return counts;
  }, [capabilityData]);

  const submitInspection = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setInspection(null);
    setInspectionError(null);
    setInspectionLoading(true);
    try {
      const result = await inspectE2EFlow(agentId, callId || undefined);
      setInspection(result);
    } catch (error) {
      setInspectionError(describeError(error));
    } finally {
      setInspectionLoading(false);
    }
  };

  return (
    <main
      aria-labelledby="parity-page-title"
      style={{
        minHeight: '100vh',
        background: '#080c12',
        color: '#e9eef5',
        padding: 'clamp(16px, 3vw, 32px)',
        boxSizing: 'border-box',
        fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif',
      }}
    >
      <div style={{ maxWidth: 1440, margin: '0 auto', display: 'grid', gap: 22 }}>
        <header style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 18, flexWrap: 'wrap' }}>
          <div>
            <p style={{ margin: '0 0 7px', color: '#97b8d7', fontSize: 12, fontWeight: 700, letterSpacing: '.12em', textTransform: 'uppercase' }}>
              Internal · evidence-backed
            </p>
            <h1 id="parity-page-title" style={{ margin: 0, fontSize: 'clamp(25px, 4vw, 38px)' }}>
              Final Retell parity
            </h1>
            <p style={{ maxWidth: 780, color: '#b8c3d0', lineHeight: 1.55 }}>
              Live route registration, tenant integration configuration, and read-only lifecycle inspection. Route presence is not an E2E pass or production-readiness claim.
            </p>
          </div>
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            style={{ border: '1px solid #42617f', borderRadius: 9, background: '#142438', color: '#e9eef5', padding: '10px 14px', cursor: loading ? 'wait' : 'pointer' }}
          >
            {loading ? 'Refreshing…' : 'Refresh evidence'}
          </button>
        </header>

        {capabilityData && (
          <section aria-label="Live route inventory" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 12 }}>
            <div style={{ background: '#111923', border: '1px solid #293544', borderRadius: 12, padding: 16 }}>
              <div style={{ color: '#91a0b1', fontSize: 12 }}>Registered API operations</div>
              <strong style={{ display: 'block', fontSize: 25, marginTop: 6 }}>{capabilityData.registered_api_operations.toLocaleString()}</strong>
            </div>
            <div style={{ background: '#111923', border: '1px solid #293544', borderRadius: 12, padding: 16 }}>
              <div style={{ color: '#91a0b1', fontSize: 12 }}>Suppressed generated handlers</div>
              <strong style={{ display: 'block', fontSize: 25, marginTop: 6 }}>{(capabilityData.suppressed_generated_placeholder_routes + capabilityData.suppressed_generic_placeholder_routes).toLocaleString()}</strong>
              <span style={{ color: '#91a0b1', fontSize: 11 }}>Endpoint-N: {capabilityData.suppressed_generated_placeholder_routes.toLocaleString()} · generic static: {capabilityData.suppressed_generic_placeholder_routes.toLocaleString()}</span>
            </div>
            <div style={{ background: '#111923', border: '1px solid #293544', borderRadius: 12, padding: 16 }}>
              <div style={{ color: '#91a0b1', fontSize: 12 }}>Route evidence status</div>
              <strong style={{ display: 'block', fontSize: 18, marginTop: 8 }}>{statusCounts.IMPLEMENTED} implemented · {statusCounts.PARTIAL} partial</strong>
              <span style={{ color: '#91a0b1', fontSize: 11 }}>{statusCounts.MISSING} missing · {statusCounts.other} other</span>
            </div>
            <div style={{ background: '#111923', border: '1px solid #293544', borderRadius: 12, padding: 16 }}>
              <div style={{ color: '#91a0b1', fontSize: 12 }}>Evidence generated</div>
              <strong style={{ display: 'block', fontSize: 14, marginTop: 10 }}>{new Date(capabilityData.generated_at).toLocaleString()}</strong>
            </div>
          </section>
        )}

        <section aria-labelledby="integrations-heading" style={{ display: 'grid', gap: 12 }}>
          <div>
            <h2 id="integrations-heading" style={{ margin: 0, fontSize: 20 }}>Integration state</h2>
            <p style={{ color: '#98a6b6', fontSize: 12, margin: '6px 0 0' }}>
              Secret values are never returned. CONNECTED means a tenant integration has a successful stored health check within the prior 15 minutes; deployment-wide credentials remain unverified until probed.
            </p>
          </div>
          {integrationError && (
            <div role="alert" style={{ border: '1px solid #824452', background: '#351921', padding: 13, borderRadius: 9 }}>
              Integration status unavailable: {integrationError}
            </div>
          )}
          {integrationData && (
            <>
              <p style={{ color: '#a8b3c1', fontSize: 12, margin: 0 }}>
                Workspace {integrationData.tenant_id} · generated {new Date(integrationData.generated_at).toLocaleString()}
              </p>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(250px, 1fr))', gap: 10 }}>
                {integrationData.items.map((item, index) => (
                  <IntegrationStateCard key={`${item.integration_type}:${item.provider}:${index}`} item={item} />
                ))}
              </div>
            </>
          )}
        </section>

        <section aria-labelledby="capabilities-heading" style={{ display: 'grid', gap: 12 }}>
          <div>
            <h2 id="capabilities-heading" style={{ margin: 0, fontSize: 20 }}>Capability evidence</h2>
            <p style={{ color: '#98a6b6', fontSize: 12, margin: '6px 0 0' }}>
              The external benchmark is Retell’s public product and API documentation, not private implementation assumptions.
            </p>
          </div>
          {capabilityError && (
            <div role="alert" style={{ border: '1px solid #824452', background: '#351921', padding: 13, borderRadius: 9 }}>
              Capability inventory unavailable: {capabilityError}
            </div>
          )}
          {capabilityData && (
            <>
              <RetellParityMatrix capabilities={capabilityData.capabilities} />
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 11 }}>
                {capabilityData.capabilities.map((item) => (
                  <CapabilityStatusCard key={item.key} item={item} />
                ))}
              </div>
              <p style={{ color: '#95a2b1', fontSize: 12, lineHeight: 1.55, margin: 0 }}>
                {capabilityData.limitation}
              </p>
            </>
          )}
          {loading && !capabilityData && <p role="status">Loading capability evidence…</p>}
        </section>

        <section aria-labelledby="inspect-heading" style={{ display: 'grid', gap: 12 }}>
          <div>
            <h2 id="inspect-heading" style={{ margin: 0, fontSize: 20 }}>Inspect a persisted lifecycle</h2>
            <p style={{ color: '#98a6b6', fontSize: 12, margin: '6px 0 0' }}>
              Supply an agent UUID and optionally a persisted call-session UUID. This performs read-only checks; it does not create test data or initiate calls.
            </p>
          </div>
          <form onSubmit={submitInspection} style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', alignItems: 'end', gap: 10 }}>
            <label style={{ display: 'grid', gap: 6, fontSize: 12 }} htmlFor="parity-agent-id">
              Agent UUID
              <input
                id="parity-agent-id"
                required
                value={agentId}
                onChange={(event) => setAgentId(event.target.value)}
                autoComplete="off"
                style={{ minWidth: 0, padding: 10, borderRadius: 8, border: '1px solid #415064', background: '#0d141d', color: '#f2f5f8' }}
              />
            </label>
            <label style={{ display: 'grid', gap: 6, fontSize: 12 }} htmlFor="parity-call-id">
              Call session UUID (optional)
              <input
                id="parity-call-id"
                value={callId}
                onChange={(event) => setCallId(event.target.value)}
                autoComplete="off"
                style={{ minWidth: 0, padding: 10, borderRadius: 8, border: '1px solid #415064', background: '#0d141d', color: '#f2f5f8' }}
              />
            </label>
            <button
              type="submit"
              disabled={inspectionLoading || !agentId.trim()}
              style={{ border: '1px solid #42617f', borderRadius: 9, background: '#142438', color: '#e9eef5', padding: '11px 14px', cursor: inspectionLoading ? 'wait' : 'pointer' }}
            >
              {inspectionLoading ? 'Inspecting…' : 'Inspect records'}
            </button>
          </form>
          {inspectionError && <div role="alert" style={{ border: '1px solid #824452', background: '#351921', padding: 13, borderRadius: 9 }}>Inspection failed: {inspectionError}</div>}
          {inspection && <E2EFlowTimeline inspection={inspection} />}
          {inspectionLoading && <p role="status">Reading tenant-scoped agent and call records…</p>}
        </section>

        <footer style={{ borderTop: '1px solid #293544', paddingTop: 14, color: '#95a2b1', fontSize: 12 }}>
          <a href="/dashboard/agents" style={{ color: '#9fcfff' }}>Agent Studio</a>
          {' · '}
          <a href="/dashboard/phone-numbers" style={{ color: '#9fcfff' }}>Phone numbers</a>
          {' · '}
          <a href="/calls" style={{ color: '#9fcfff' }}>Calls</a>
        </footer>
      </div>
    </main>
  );
}

export default FinalParityPage;
