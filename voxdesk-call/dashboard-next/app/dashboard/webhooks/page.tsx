"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, request } from "@/lib/api";
import { getToken } from "@/lib/auth";

interface WebhookSubscriptionRecord {
  id: string;
  url: string;
  events: string[];
  is_active: boolean;
  description: string | null;
  secret_preview: string;
  created_at: string | null;
}

/** Mirrors the `WebhookDelivery` projection in
 *  `app/api/webhook_lifecycle_routes.py::_delivery`. The response body is not
 *  part of the list payload — it is only returned by the single-delivery route,
 *  so the table shows the error category instead of pretending to have it. */
interface WebhookDeliveryRecord {
  id: string;
  endpoint_id: string;
  subscription_id?: string;
  event_id: string | null;
  status: string;
  attempts: number;
  attempt_limit: number;
  http_status: number | null;
  latency_ms: number | null;
  last_error_category: string | null;
  is_dlq: boolean;
  created_at: string | null;
  delivered_at: string | null;
}

const DEFAULT_EVENTS = [
  "call_started",
  "call_ended",
  "call_analyzed",
  "transcript_updated",
  "transfer_started",
];

export default function WebhooksPage() {
  const [subscriptions, setSubscriptions] = useState<WebhookSubscriptionRecord[]>(
    [],
  );
  const [deliveries, setDeliveries] = useState<WebhookDeliveryRecord[]>([]);
  const [dlq, setDlq] = useState<WebhookDeliveryRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Create form state
  const [webhookUrl, setWebhookUrl] = useState(
    "https://hooks.example.com/voxdesk/events",
  );
  const [webhookDesc, setWebhookDesc] = useState("Production call events");
  const [selectedEvents, setSelectedEvents] = useState<string[]>([
    "call_started",
    "call_ended",
    "call_analyzed",
  ]);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const subsRes = await request<{
        subscriptions: WebhookSubscriptionRecord[];
      }>("/api/webhooks");
      const subs = subsRes.subscriptions ?? [];
      setSubscriptions(subs);
      // Deliveries are tenant-wide, but the dead-letter queue is served per
      // endpoint: there is no global DLQ listing, so one endpoint is chosen to
      // scope it. `deliveries/recent` is the tenant-wide feed.
      const [delivRes, dlqRes] = await Promise.all([
        request<{ deliveries: WebhookDeliveryRecord[] }>(
          "/api/webhooks/deliveries/recent",
        ),
        subs[0]
          ? request<{ deliveries: WebhookDeliveryRecord[] }>(
              `/api/webhooks/${encodeURIComponent(subs[0].id)}/dlq`,
            )
          : Promise.resolve({ deliveries: [] as WebhookDeliveryRecord[] }),
      ]);
      setDeliveries(delivRes.deliveries ?? []);
      setDlq(dlqRes.deliveries ?? []);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to load webhooks",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!getToken()) {
      setLoading(false);
      return;
    }
    void loadAll();
  }, [loadAll]);

  function toggleEvent(ev: string) {
    setSelectedEvents((prev) =>
      prev.includes(ev) ? prev.filter((item) => item !== ev) : [...prev, ev],
    );
  }

  async function handleCreateSubscription(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    if (!webhookUrl.trim()) {
      setError("Webhook endpoint URL is required.");
      return;
    }
    try {
      const created = await request<
        WebhookSubscriptionRecord & { secret?: string }
      >("/api/webhooks", {
        method: "POST",
        body: JSON.stringify({
          url: webhookUrl.trim(),
          events: selectedEvents,
          description: webhookDesc.trim(),
          is_active: true,
        }),
      });
      setSubscriptions((prev) => [created, ...prev]);
      if (created.secret) {
        setNotice(`Signing secret (save now): ${created.secret}`);
      }
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Failed to create webhook subscription",
      );
    }
  }

  async function handleTestSend(subId: string) {
    setError(null);
    setNotice(null);
    try {
      await request(`/api/webhooks/${encodeURIComponent(subId)}/test`, {
        method: "POST",
        body: JSON.stringify({ event_type: "call_ended" }),
      });
      setNotice("Test webhook queued for delivery.");
      await loadAll();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Test send failed");
    }
  }

  async function handleRotateSecret(subId: string) {
    setError(null);
    setNotice(null);
    try {
      const res = await request<{ secret: string; secret_preview: string }>(
        `/api/webhooks/${encodeURIComponent(subId)}/rotate-secret`,
        { method: "POST" },
      );
      setNotice(`Rotated signing secret: ${res.secret}`);
      await loadAll();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to rotate secret",
      );
    }
  }

  async function handleRetryDelivery(deliveryId: string) {
    setError(null);
    setNotice(null);
    try {
      const delivery = deliveries.find((d) => d.id === deliveryId);
      if (!delivery) {
        setError("Delivery not found; reload the page and try again.");
        return;
      }
      await request(
        `/api/webhooks/${encodeURIComponent(delivery.subscription_id ?? "")}/deliveries/${encodeURIComponent(deliveryId)}/retry`,
        { method: "POST" },
      );
      setNotice(`Delivery ${deliveryId} queued for retry.`);
      await loadAll();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Retry failed");
    }
  }

  async function handleRedriveDlq(endpointId: string) {
    setError(null);
    setNotice(null);
    try {
      const res = await request<{ queued: number; remaining: number }>(
        `/api/webhooks/${encodeURIComponent(endpointId)}/dlq/replay-all`,
        { method: "POST" },
      );
      setNotice(
        `Queued ${res.queued} dead-letter delivery(ies) for replay; ${res.remaining} remaining.`,
      );
      await loadAll();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "DLQ redrive failed");
    }
  }

  return (
    <div data-testid="webhooks-page">
      <header className="page-head">
        <div>
          <h1>Webhook Subscriptions, Deliveries &amp; DLQ</h1>
          <p className="muted">
            Manage HMAC-signed webhook endpoints, inspect delivery status/latency/response, trigger test sends, rotate secrets, and replay the dead-letter queue. The DLQ is served per endpoint, so it is scoped to the first subscription and the button replays that endpoint's queue.
          </p>
        </div>
      </header>

      {error ? (
        <div className="error-banner" role="alert">
          {error}
        </div>
      ) : null}

      {notice ? (
        <div className="card" data-testid="webhook-notice-banner">
          {notice}
        </div>
      ) : null}

      <section className="card" data-testid="create-webhook-card">
        <h2>Create Webhook Subscription</h2>
        <form
          onSubmit={handleCreateSubscription}
          style={{ display: "grid", gap: "0.75rem" }}
        >
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
              gap: "0.75rem",
            }}
          >
            <label>
              Target Endpoint URL (HTTPS)
              <input
                type="url"
                data-testid="webhook-url-input"
                value={webhookUrl}
                onChange={(e) => setWebhookUrl(e.target.value)}
              />
            </label>
            <label>
              Description
              <input
                type="text"
                value={webhookDesc}
                onChange={(e) => setWebhookDesc(e.target.value)}
              />
            </label>
          </div>

          <div>
            <span>Subscribed Event Types:</span>
            <div
              style={{
                display: "flex",
                gap: "0.75rem",
                flexWrap: "wrap",
                marginTop: "0.35rem",
              }}
            >
              {DEFAULT_EVENTS.map((ev) => (
                <label key={ev} style={{ display: "flex", gap: "0.35rem" }}>
                  <input
                    type="checkbox"
                    checked={selectedEvents.includes(ev)}
                    onChange={() => toggleEvent(ev)}
                  />
                  <code>{ev}</code>
                </label>
              ))}
            </div>
          </div>

          <div>
            <button
              type="submit"
              className="btn primary"
              data-testid="create-webhook-submit-btn"
            >
              Create Subscription
            </button>
          </div>
        </form>
      </section>

      <section className="card" data-testid="webhook-subscriptions-card">
        <h2>Subscriptions ({subscriptions.length})</h2>
        {loading ? (
          <div className="loading">Loading subscriptions…</div>
        ) : subscriptions.length === 0 ? (
          <div className="empty">No webhook subscriptions configured.</div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Endpoint URL</th>
                <th>Events</th>
                <th>Secret</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {subscriptions.map((sub) => (
                <tr key={sub.id}>
                  <td>
                    <code>{sub.url}</code>
                    {sub.description ? (
                      <div className="muted">{sub.description}</div>
                    ) : null}
                  </td>
                  <td>{sub.events.join(", ")}</td>
                  <td>
                    <code>{sub.secret_preview}</code>
                  </td>
                  <td>{sub.is_active ? "Active" : "Paused"}</td>
                  <td>
                    <div style={{ display: "flex", gap: "0.35rem" }}>
                      <button
                        type="button"
                        className="btn primary"
                        data-testid={`test-send-btn-${sub.id}`}
                        onClick={() => handleTestSend(sub.id)}
                      >
                        Test Send
                      </button>
                      <button
                        type="button"
                        className="btn"
                        data-testid={`rotate-secret-btn-${sub.id}`}
                        onClick={() => handleRotateSecret(sub.id)}
                      >
                        Rotate Secret
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="card" data-testid="webhook-deliveries-card">
        <h2>Recent Deliveries ({deliveries.length})</h2>
        {deliveries.length === 0 ? (
          <div className="empty">No webhook deliveries recorded yet.</div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Event</th>
                <th>Status</th>
                <th>HTTP / Latency</th>
                <th>Attempts</th>
                <th>Response / Error</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {deliveries.map((d) => (
                <tr key={d.id}>
                  <td>
                    <code>{d.event_id ?? "—"}</code>
                  </td>
                  <td>
                    <span className={`badge ${d.status}`}>{d.status}</span>
                  </td>
                  <td>
                    {d.http_status ?? "—"}
                    {d.latency_ms != null ? ` (${d.latency_ms}ms)` : ""}
                  </td>
                  <td>{d.attempts}/{d.attempt_limit}</td>
                  <td>{d.last_error_category ?? "—"}</td>
                  <td>
                    <button
                      type="button"
                      className="btn"
                      data-testid={`retry-delivery-btn-${d.id}`}
                      onClick={() => handleRetryDelivery(d.id)}
                    >
                      Retry
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="card" data-testid="webhook-dlq-card">
        <h2>Dead-Letter Queue (DLQ) ({dlq.length})</h2>
        {dlq.length === 0 ? (
          <div className="empty">Dead-letter queue is empty.</div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Event</th>
                <th>Attempts</th>
                <th>Last Error</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {dlq.map((item) => (
                <tr key={item.id}>
                  <td>
                    <code>{item.event_id ?? "—"}</code>
                  </td>
                  <td>{item.attempts}/{item.attempt_limit}</td>
                  <td>{item.last_error_category ?? "exhausted retries"}</td>
                  <td>
                    <button
                      type="button"
                      className="btn primary"
                      data-testid={`redrive-dlq-btn-${item.endpoint_id}`}
                      onClick={() => handleRedriveDlq(item.endpoint_id)}
                    >
                      Redrive
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
