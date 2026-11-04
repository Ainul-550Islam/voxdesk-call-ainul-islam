/**
 * dashboard/src/pages/webhooks/WebhooksPage.tsx
 * Endpoint list, delivery log, replay and secret rotation.
 *
 * Data: `api/webhooks.ts` → app/api/webhook_lifecycle_routes.py.
 *
 * Two behaviours worth stating because they are easy to get wrong:
 *   • `rotate-secret` returns the new secret exactly once. It is displayed in a
 *     dismissible panel and never stored in component state beyond that render.
 *   • Replay is refused by the backend for deliveries that already succeeded
 *     (`acknowledged`), so the button is disabled for those rather than letting
 *     the operator trigger a 409.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  WebhookDelivery,
  WebhookEndpoint,
  WebhookSecretRotation,
  createWebhook,
  deleteWebhook,
  disableWebhook,
  enableWebhook,
  listDeliveries,
  listWebhooks,
  replayAllDlq,
  retryDelivery,
  rotateSecret,
  sendTestEvent,
} from '../../api/webhooks';
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

const TEST_ID = 'webhooks-page';

export function WebhooksPage() {
  const [endpoints, setEndpoints] = useState<WebhookEndpoint[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [deliveries, setDeliveries] = useState<WebhookDelivery[]>([]);
  const [deliveryTotal, setDeliveryTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [deliveriesLoading, setDeliveriesLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [rotation, setRotation] = useState<WebhookSecretRotation | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [url, setUrl] = useState('');
  const [description, setDescription] = useState('');
  const [events, setEvents] = useState('');
  const [statusFilter, setStatusFilter] = useState('');

  const selected = endpoints.find((endpoint) => endpoint.id === selectedId) ?? null;

  const loadEndpoints = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await listWebhooks({ limit: 100 });
      setEndpoints(page.endpoints);
      setSelectedId((current) => current ?? page.endpoints[0]?.id ?? null);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadDeliveries = useCallback(
    async (endpointId: string, status: string) => {
      setDeliveriesLoading(true);
      try {
        const page = await listDeliveries(endpointId, { limit: 50, status: status || null });
        setDeliveries(page.deliveries);
        setDeliveryTotal(page.total);
      } catch (e) {
        setActionError(e);
        setDeliveries([]);
      } finally {
        setDeliveriesLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    void loadEndpoints();
  }, [loadEndpoints]);

  useEffect(() => {
    if (selectedId) void loadDeliveries(selectedId, statusFilter);
  }, [selectedId, statusFilter, loadDeliveries]);

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

  function create(event: React.FormEvent) {
    event.preventDefault();
    void run('create', async () => {
      const created = await createWebhook({
        url: url.trim(),
        description: description.trim(),
        events: events
          .split(',')
          .map((value) => value.trim())
          .filter(Boolean),
        is_active: true,
      });
      setNotice(`Endpoint ${created.id} created.`);
      setUrl('');
      setDescription('');
      setEvents('');
      await loadEndpoints();
      setSelectedId(created.id);
    });
  }

  if (loading) return <LoadingState label="Loading webhook endpoints…" />;

  return (
    <main data-testid={TEST_ID} style={{ padding: 20, color: '#e9eef5' }}>
      <h1 style={{ fontSize: 20, margin: '0 0 4px' }}>Webhooks</h1>
      <p style={{ margin: '0 0 16px', fontSize: 12.5, color: '#8fa3b8' }}>
        Signed deliveries from the durable outbox. A delivery that already succeeded is never resent blindly.
      </p>

      <ErrorState error={error} onRetry={() => void loadEndpoints()} />
      <ErrorState error={actionError} />
      {notice && (
        <div role="status" data-testid="webhooks-notice" style={{ background: '#0f2418', border: '1px solid #1d4d31', borderRadius: 10, padding: 12, marginBottom: 12, fontSize: 13, color: '#c9f3da' }}>
          {notice}
        </div>
      )}
      {rotation && (
        <div role="status" data-testid="webhook-rotated-secret" style={{ background: '#2a1f0b', border: '1px solid #7a5a12', borderRadius: 10, padding: 12, marginBottom: 12, fontSize: 12.5, color: '#f7e6bd' }}>
          <strong>New signing secret (version {rotation.secret_version}) — shown once.</strong>
          <pre style={{ margin: '6px 0', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{rotation.secret}</pre>
          <button style={subtleButtonStyle} onClick={() => setRotation(null)}>
            Dismiss
          </button>
        </div>
      )}

      <ConsoleCard title="Add endpoint" testId="webhook-add">
        <form onSubmit={create}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: 12 }}>
            <Field label="Endpoint URL (https, 10–500 chars)">
              <input style={inputStyle} data-testid="webhook-url" value={url} onChange={(e) => setUrl(e.target.value)} required minLength={10} />
            </Field>
            <Field label="Description">
              <input style={inputStyle} value={description} onChange={(e) => setDescription(e.target.value)} />
            </Field>
            <Field label="Events (comma separated, empty = all)">
              <input style={inputStyle} data-testid="webhook-events" value={events} onChange={(e) => setEvents(e.target.value)} placeholder="call.completed, lead.created" />
            </Field>
          </div>
          <button type="submit" style={buttonStyle} disabled={busy === 'create'} data-testid="webhook-create">
            {busy === 'create' ? 'Creating…' : 'Create endpoint'}
          </button>
        </form>
      </ConsoleCard>

      <ConsoleCard title={`Endpoints (${endpoints.length})`} testId="webhook-endpoints">
        {endpoints.length === 0 ? (
          <EmptyState title="No endpoints" detail="Create one to receive signed lifecycle events." />
        ) : (
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={thStyle}>URL</th>
                <th style={thStyle}>Events</th>
                <th style={thStyle}>State</th>
                <th style={thStyle}>Secret version</th>
                <th style={thStyle}>Created</th>
                <th style={thStyle} />
              </tr>
            </thead>
            <tbody>
              {endpoints.map((endpoint) => (
                <tr key={endpoint.id} data-testid={`webhook-row-${endpoint.id}`}>
                  <td style={tdStyle}>
                    <button
                      onClick={() => setSelectedId(endpoint.id)}
                      style={{ background: 'none', border: 'none', color: selectedId === endpoint.id ? '#fff' : '#6ea8ff', cursor: 'pointer', padding: 0, fontSize: 12.5 }}
                    >
                      {endpoint.url}
                    </button>
                    {endpoint.description && <div style={{ fontSize: 10.5, color: '#5d7186' }}>{endpoint.description}</div>}
                  </td>
                  <td style={tdStyle}>{endpoint.events.length === 0 ? 'all' : endpoint.events.join(', ')}</td>
                  <td style={tdStyle}>
                    <Badge tone={endpoint.is_active ? 'ok' : 'neutral'}>{endpoint.is_active ? 'active' : 'paused'}</Badge>
                  </td>
                  <td style={tdStyle}>{endpoint.secret_version ?? '—'}</td>
                  <td style={tdStyle}>{formatDate(endpoint.created_at)}</td>
                  <td style={{ ...tdStyle, whiteSpace: 'nowrap' }}>
                    <button
                      style={subtleButtonStyle}
                      disabled={busy !== null}
                      onClick={() =>
                        void run(`toggle:${endpoint.id}`, async () => {
                          const next = endpoint.is_active ? await disableWebhook(endpoint.id) : await enableWebhook(endpoint.id);
                          setEndpoints((prev) => prev.map((row) => (row.id === next.id ? next : row)));
                        })
                      }
                    >
                      {endpoint.is_active ? 'Disable' : 'Enable'}
                    </button>{' '}
                    <button
                      style={subtleButtonStyle}
                      data-testid={`rotate-${endpoint.id}`}
                      disabled={busy !== null}
                      onClick={() =>
                        void run(`rotate:${endpoint.id}`, async () => {
                          const result = await rotateSecret(endpoint.id);
                          setRotation(result);
                          await loadEndpoints();
                        })
                      }
                    >
                      Rotate secret
                    </button>{' '}
                    <button
                      style={subtleButtonStyle}
                      disabled={busy !== null}
                      onClick={() =>
                        void run(`test:${endpoint.id}`, async () => {
                          await sendTestEvent(endpoint.id);
                          setNotice(`Test event queued for ${endpoint.url}.`);
                          await loadDeliveries(endpoint.id, statusFilter);
                        })
                      }
                    >
                      Send test
                    </button>{' '}
                    <button
                      style={subtleButtonStyle}
                      disabled={busy !== null}
                      onClick={() =>
                        void run(`delete:${endpoint.id}`, async () => {
                          await deleteWebhook(endpoint.id);
                          setNotice(`Endpoint ${endpoint.id} deleted.`);
                          setSelectedId(null);
                          await loadEndpoints();
                        })
                      }
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </ConsoleCard>

      <ConsoleCard
        title={selected ? `Deliveries for ${selected.url}` : 'Deliveries'}
        testId="webhook-deliveries"
        description={selected ? `${deliveryTotal} delivery record(s)` : 'Select an endpoint to inspect its delivery log.'}
        actions={
          <div style={{ display: 'flex', gap: 6 }}>
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} style={{ ...inputStyle, width: 140, padding: '4px 6px' }} data-testid="delivery-status-filter">
              <option value="">all statuses</option>
              <option value="succeeded">succeeded</option>
              <option value="pending">pending</option>
              <option value="retrying">retrying</option>
              <option value="dead_letter">dead_letter</option>
            </select>
            <button
              style={subtleButtonStyle}
              disabled={!selected || busy !== null}
              onClick={() =>
                selected &&
                void run('replay-all', async () => {
                  const result = await replayAllDlq(selected.id);
                  setNotice(`Dead-letter replay requested: ${JSON.stringify(result)}`);
                  await loadDeliveries(selected.id, statusFilter);
                })
              }
            >
              Replay DLQ
            </button>
          </div>
        }
      >
        {!selected ? (
          <EmptyState title="No endpoint selected" />
        ) : deliveriesLoading ? (
          <LoadingState label="Loading deliveries…" />
        ) : deliveries.length === 0 ? (
          <EmptyState title="No deliveries" detail="Deliveries appear after the first event is dispatched to this endpoint." />
        ) : (
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={thStyle}>Delivery</th>
                <th style={thStyle}>Event</th>
                <th style={thStyle}>Status</th>
                <th style={thStyle}>HTTP</th>
                <th style={thStyle}>Attempts</th>
                <th style={thStyle}>Latency</th>
                <th style={thStyle}>Created</th>
                <th style={thStyle} />
              </tr>
            </thead>
            <tbody>
              {deliveries.map((delivery) => (
                <tr key={delivery.id} data-testid={`delivery-row-${delivery.id}`}>
                  <td style={tdStyle}>{delivery.id.slice(0, 8)}</td>
                  <td style={tdStyle}>{delivery.event_id ?? '—'}</td>
                  <td style={tdStyle}>
                    <Badge tone={delivery.status === 'succeeded' ? 'ok' : delivery.is_dlq ? 'bad' : 'warn'}>{delivery.status}</Badge>
                  </td>
                  <td style={tdStyle}>{delivery.http_status ?? '—'}</td>
                  <td style={tdStyle}>
                    {delivery.attempts}/{delivery.attempt_limit}
                  </td>
                  <td style={tdStyle}>{delivery.latency_ms === null ? '—' : `${delivery.latency_ms} ms`}</td>
                  <td style={tdStyle}>{formatDate(delivery.created_at)}</td>
                  <td style={tdStyle}>
                    <button
                      style={subtleButtonStyle}
                      data-testid={`replay-${delivery.id}`}
                      disabled={busy !== null || delivery.status === 'succeeded'}
                      title={delivery.status === 'succeeded' ? 'Acknowledged deliveries are not resent' : 'Re-send through the ledger'}
                      onClick={() =>
                        void run(`replay:${delivery.id}`, async () => {
                          const result = await retryDelivery(selected.id, delivery.id);
                          setNotice(`Delivery ${delivery.id} re-queued (status ${result.status}).`);
                          await loadDeliveries(selected.id, statusFilter);
                        })
                      }
                    >
                      Replay
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </ConsoleCard>
    </main>
  );
}

export default WebhooksPage;
