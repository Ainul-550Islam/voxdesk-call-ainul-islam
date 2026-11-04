/**
 * dashboard/src/pages/integrations/IntegrationsConsolePage.tsx
 * Authenticated integration management (the marketing `IntegrationsPage` is a
 * separate, public, registry-only page — this one talks to real tenant data).
 *
 * Data: `api/integrations.ts` →
 *   GET  /api/integrations/crm/providers   → provider catalogue + credential fields
 *   GET  /api/integrations/crm             → connected state per provider
 *   PUT  /api/integrations/crm/{provider}  → connect/update (credentials write-only)
 *   POST /api/integrations/crm/{provider}/disconnect | /test
 *   GET  /api/integrations/crm/syncs       → sync-run feed
 *
 * The connect form builds its inputs from `credential_fields`/`config_fields`
 * that the server advertises for that provider, so the console cannot ask for a
 * field the adapter does not read. Credentials are sent once and never rendered
 * back — every response carries `connected` and `last_health_ok` instead.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Integration,
  IntegrationHealth,
  ProviderInfo,
  SyncRun,
  connectIntegration,
  disconnectIntegration,
  listIntegrations,
  listProviders,
  listSyncRuns,
  setFieldMappings,
  testIntegration,
} from '../../api/integrations';
import {
  Badge,
  ConsoleCard,
  EmptyState,
  ErrorState,
  Field,
  LoadingState,
  buttonStyle,
  formatDate,
  inputStyle,
  subtleButtonStyle,
  tableStyle,
  tdStyle,
  thStyle,
} from '../../components/layout/ConsoleStates';

export function IntegrationsConsolePage() {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [syncs, setSyncs] = useState<SyncRun[]>([]);
  const [syncTotal, setSyncTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [activeProvider, setActiveProvider] = useState<string | null>(null);
  const [credentialDraft, setCredentialDraft] = useState<Record<string, string>>({});
  const [configDraft, setConfigDraft] = useState<Record<string, string>>({});
  const [mappingDraft, setMappingDraft] = useState('');
  const [health, setHealth] = useState<Record<string, IntegrationHealth>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [catalogue, connected, syncPage] = await Promise.all([
        listProviders(),
        listIntegrations(),
        listSyncRuns({ limit: 25 }),
      ]);
      setProviders(catalogue.providers);
      setIntegrations(connected.integrations);
      setSyncs(syncPage.syncs);
      setSyncTotal(syncPage.total);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const byProvider = useMemo(() => {
    const map: Record<string, Integration> = {};
    for (const integration of integrations) map[integration.provider] = integration;
    return map;
  }, [integrations]);

  const selectedProvider = providers.find((provider) => provider.provider === activeProvider) ?? null;

  async function run(key: string, action: () => Promise<void>) {
    setBusy(key);
    setActionError(null);
    try {
      await action();
    } catch (e) {
      setActionError(e);
    } finally {
      setBusy(null);
    }
  }

  function connect(event: React.FormEvent) {
    event.preventDefault();
    if (!selectedProvider) return;
    void run(`connect:${selectedProvider.provider}`, async () => {
      const updated = await connectIntegration(selectedProvider.provider, {
        is_enabled: true,
        credentials: Object.keys(credentialDraft).length > 0 ? credentialDraft : undefined,
        config: Object.keys(configDraft).length > 0 ? configDraft : undefined,
      });
      setIntegrations((prev) => [...prev.filter((row) => row.provider !== updated.provider), updated]);
      setCredentialDraft({});
      setConfigDraft({});
      setNotice(`${updated.provider}: ${updated.connected ? 'connected' : 'saved, not yet connected'}.`);
    });
  }

  function saveMappings() {
    if (!selectedProvider) return;
    let parsed: Record<string, unknown>;
    try {
      parsed = mappingDraft.trim() ? JSON.parse(mappingDraft) : {};
    } catch {
      setActionError(new Error('Field mappings must be valid JSON.'));
      return;
    }
    void run(`mapping:${selectedProvider.provider}`, async () => {
      const updated = await setFieldMappings(selectedProvider.provider, parsed);
      setIntegrations((prev) => [...prev.filter((row) => row.provider !== updated.provider), updated]);
      setNotice(`${updated.provider}: field mappings saved.`);
    });
  }

  if (loading) return <LoadingState label="Loading integrations…" />;

  return (
    <main data-testid="integrations-console-page" style={{ padding: 20, color: '#e9eef5' }}>
      <h1 style={{ fontSize: 20, margin: '0 0 4px' }}>Integrations</h1>
      <p style={{ margin: '0 0 16px', fontSize: 12.5, color: '#8fa3b8' }}>
        Authenticated CRM connections for this workspace. The public catalogue page describes adapters; this page operates them.
      </p>

      <ErrorState error={error} onRetry={() => void load()} />
      <ErrorState error={actionError} />
      {notice && (
        <div role="status" style={{ background: '#0f2418', border: '1px solid #1d4d31', borderRadius: 10, padding: 12, marginBottom: 12, fontSize: 13, color: '#c9f3da' }}>
          {notice}
        </div>
      )}

      <ConsoleCard title="Adapters" testId="integrations-adapters">
        {providers.length === 0 ? (
          <EmptyState title="No adapters registered" detail="The provider catalogue is empty for this deployment." />
        ) : (
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={thStyle}>Provider</th>
                <th style={thStyle}>Capabilities</th>
                <th style={thStyle}>State</th>
                <th style={thStyle}>Health</th>
                <th style={thStyle} />
              </tr>
            </thead>
            <tbody>
              {providers.map((provider) => {
                const integration = byProvider[provider.provider];
                const healthResult = health[provider.provider];
                return (
                  <tr key={provider.provider} data-testid={`integration-row-${provider.provider}`}>
                    <td style={tdStyle}>{provider.provider}</td>
                    <td style={{ ...tdStyle, color: '#8fa3b8' }}>{provider.capabilities.join(', ') || '—'}</td>
                    <td style={tdStyle}>
                      <Badge tone={integration?.connected ? 'ok' : integration?.is_enabled ? 'warn' : 'neutral'}>
                        {integration?.connected ? 'connected' : integration?.is_enabled ? 'configured' : 'not connected'}
                      </Badge>
                      {integration?.last_error && <div style={{ fontSize: 10.5, color: '#f2a1a1' }}>{integration.last_error}</div>}
                    </td>
                    <td style={{ ...tdStyle, fontSize: 11.5 }}>
                      {healthResult
                        ? `${healthResult.connected ? 'ok' : 'failing'} · ${healthResult.latency_ms} ms · ${healthResult.safe_message}`
                        : integration?.last_health_ok === null || integration?.last_health_ok === undefined
                          ? '—'
                          : `${integration.last_health_ok ? 'ok' : 'failing'} @ ${formatDate(integration.last_health_check_at)}`}
                    </td>
                    <td style={{ ...tdStyle, whiteSpace: 'nowrap' }}>
                      <button style={subtleButtonStyle} onClick={() => setActiveProvider(provider.provider)}>
                        Configure
                      </button>{' '}
                      <button
                        style={subtleButtonStyle}
                        disabled={busy !== null || !integration}
                        onClick={() =>
                          void run(`test:${provider.provider}`, async () => {
                            const result = await testIntegration(provider.provider);
                            setHealth((prev) => ({ ...prev, [provider.provider]: result }));
                          })
                        }
                      >
                        Test
                      </button>{' '}
                      <button
                        style={subtleButtonStyle}
                        disabled={busy !== null || !integration?.connected}
                        onClick={() =>
                          void run(`disconnect:${provider.provider}`, async () => {
                            const updated = await disconnectIntegration(provider.provider);
                            setIntegrations((prev) => [...prev.filter((row) => row.provider !== updated.provider), updated]);
                            setNotice(`${updated.provider}: disconnected.`);
                          })
                        }
                      >
                        Disconnect
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </ConsoleCard>

      {selectedProvider && (
        <ConsoleCard
          title={`Configure ${selectedProvider.provider}`}
          testId="integration-configure"
          actions={
            <button style={subtleButtonStyle} onClick={() => setActiveProvider(null)}>
              Close
            </button>
          }
        >
          <form onSubmit={connect}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: 12 }}>
              {selectedProvider.credential_fields.map((field) => (
                <Field key={`cred-${field}`} label={`${field} (write-only)`}>
                  <input
                    style={inputStyle}
                    type="password"
                    data-testid={`credential-${field}`}
                    value={credentialDraft[field] ?? ''}
                    onChange={(e) => setCredentialDraft({ ...credentialDraft, [field]: e.target.value })}
                    placeholder="stored encrypted, never returned"
                  />
                </Field>
              ))}
              {selectedProvider.config_fields.map((field) => (
                <Field key={`cfg-${field}`} label={field}>
                  <input
                    style={inputStyle}
                    value={configDraft[field] ?? ''}
                    onChange={(e) => setConfigDraft({ ...configDraft, [field]: e.target.value })}
                  />
                </Field>
              ))}
            </div>
            <button type="submit" style={buttonStyle} disabled={busy !== null} data-testid="integration-connect">
              {busy?.startsWith('connect') ? 'Saving…' : 'Connect / update'}
            </button>
            <p style={{ fontSize: 11.5, color: '#8fa3b8', marginTop: 8 }}>
              Leaving every credential blank keeps the stored secret untouched — the API treats an omitted
              <code> credentials</code> object as “no change”.
            </p>
          </form>

          <div style={{ marginTop: 16 }}>
            <Field label="Field mappings (JSON)">
              <textarea
                style={{ ...inputStyle, minHeight: 80, fontFamily: 'monospace' }}
                value={mappingDraft}
                onChange={(e) => setMappingDraft(e.target.value)}
                placeholder='{"email": "email_address", "phone": "mobilephone"}'
              />
            </Field>
            <button style={subtleButtonStyle} onClick={saveMappings} disabled={busy !== null}>
              Save mappings
            </button>
          </div>
        </ConsoleCard>
      )}

      <ConsoleCard title={`Sync runs (${syncTotal})`} testId="integration-syncs" description="Most recent 25 records from the durable CRM sync ledger.">
        {syncs.length === 0 ? (
          <EmptyState title="No sync runs" detail="Sync records appear after the first outbound write to a connected provider." />
        ) : (
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={thStyle}>Provider</th>
                <th style={thStyle}>Entity</th>
                <th style={thStyle}>Event</th>
                <th style={thStyle}>Status</th>
                <th style={thStyle}>Attempts</th>
                <th style={thStyle}>Synced</th>
                <th style={thStyle}>Last error</th>
              </tr>
            </thead>
            <tbody>
              {syncs.map((sync) => (
                <tr key={sync.id} data-testid={`sync-row-${sync.id}`}>
                  <td style={tdStyle}>{sync.provider}</td>
                  <td style={tdStyle}>
                    {sync.entity_type}:{sync.entity_id.slice(0, 8)}
                  </td>
                  <td style={tdStyle}>{sync.event_type ?? '—'}</td>
                  <td style={tdStyle}>
                    <Badge tone={sync.status === 'succeeded' ? 'ok' : sync.status === 'failed' ? 'bad' : 'warn'}>{sync.status}</Badge>
                  </td>
                  <td style={tdStyle}>{sync.attempt_count}</td>
                  <td style={tdStyle}>{formatDate(sync.synced_at)}</td>
                  <td style={{ ...tdStyle, color: '#8fa3b8' }}>{sync.last_error ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </ConsoleCard>
    </main>
  );
}

export default IntegrationsConsolePage;
