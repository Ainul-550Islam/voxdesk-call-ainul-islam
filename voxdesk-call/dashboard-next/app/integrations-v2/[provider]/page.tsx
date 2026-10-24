"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  int2Api,
  type CatalogProvider,
  type ConnectionMasked,
  type SyncRunRow,
} from "../lib/api";
import { ConnectButton } from "../components/ConnectButton";
import { StatusBadge } from "../components/StatusBadge";
import { SyncRunTable } from "../components/SyncRunTable";

export default function ProviderDetailPage() {
  const params = useParams<{ provider: string }>();
  const slug = String(params?.provider || "");

  const [descriptor, setDescriptor] = useState<CatalogProvider | null>(null);
  const [connection, setConnection] = useState<ConnectionMasked | null>(null);
  const [runs, setRuns] = useState<SyncRunRow[]>([]);
  const [instanceUrl, setInstanceUrl] = useState("");
  const [region, setRegion] = useState("us");
  const [apiKey, setApiKey] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [orgId, setOrgId] = useState("");
  const [statusMessage, setStatusMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    if (!slug) return;
    setLoading(true);
    setError("");
    try {
      const [cat, conns] = await Promise.all([
        int2Api.getCatalog(),
        int2Api.listConnections(),
      ]);
      const found = cat.providers.find((p) => p.slug === slug) || null;
      setDescriptor(found);
      const activeConn =
        conns.connections.find((c) => c.provider === slug) || null;
      setConnection(activeConn);
      if (activeConn) {
        setInstanceUrl(activeConn.instance_url || "");
        setRegion(activeConn.region || "us");
        const syncRes = await int2Api.listSyncRuns(activeConn.id);
        setRuns(syncRes.sync_runs);
      } else {
        setRuns([]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load provider");
    } finally {
      setLoading(false);
    }
  }, [slug]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const handleApiKeySave = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setStatusMessage("");
    try {
      const res = await int2Api.connectProvider(slug, {
        instance_url: instanceUrl,
        region,
        credentials: { api_key: apiKey },
        webhook_secret: webhookSecret,
        config: orgId ? { org_id: orgId } : {},
        verify_now: true,
      });
      if (res.connection) {
        setConnection(res.connection);
        setApiKey("");
        setStatusMessage("API key stored and health verified.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "API key connection failed");
    }
  };

  const handleTest = async () => {
    if (!connection) return;
    setError("");
    setStatusMessage("");
    try {
      const res = await int2Api.testConnection(connection.id);
      setConnection(res.connection);
      setStatusMessage(
        `Connection healthy (${res.health.latency_ms} ms): ${res.health.detail}`
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Health check failed");
      await reload();
    }
  };

  const handleDisconnect = async () => {
    if (!connection) return;
    setError("");
    setStatusMessage("");
    try {
      await int2Api.disconnectConnection(connection.id);
      setConnection(null);
      setRuns([]);
      setStatusMessage("Connection disconnected.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Disconnect failed");
    }
  };

  const handleTriggerSync = async () => {
    if (!connection) return;
    setError("");
    setStatusMessage("");
    const entity = descriptor?.category === "helpdesk" ? "Ticket" : "Contact";
    try {
      const res = await int2Api.triggerSync(connection.id, entity);
      setRuns((prev) => [res.sync_run, ...prev]);
      setStatusMessage(
        `Sync finished (${res.sync_run.status}): ${res.sync_run.records_upserted} upserted.`
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sync failed");
    }
  };

  return (
    <main style={{ maxWidth: 980, margin: "0 auto", padding: "1.75rem" }}>
      <div style={{ marginBottom: "1rem" }}>
        <Link href="/integrations-v2">← Back to Catalog</Link>
      </div>

      {loading && <p>Loading {slug}…</p>}
      {error && (
        <div
          role="alert"
          style={{
            padding: "0.75rem 1rem",
            background: "#fef2f2",
            color: "#991b1b",
            borderRadius: 8,
            marginBottom: "1rem",
          }}
        >
          {error}
        </div>
      )}
      {statusMessage && (
        <div
          style={{
            padding: "0.75rem 1rem",
            background: "#ecfdf5",
            color: "#065f46",
            borderRadius: 8,
            marginBottom: "1rem",
          }}
        >
          {statusMessage}
        </div>
      )}

      {descriptor && (
        <section
          style={{
            border: "1px solid #e2e8f0",
            borderRadius: 10,
            padding: "1.25rem",
            marginBottom: "1.5rem",
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              gap: "0.75rem",
            }}
          >
            <h1 style={{ margin: 0 }}>{descriptor.display_name}</h1>
            <div style={{ display: "flex", gap: "0.5rem" }}>
              <StatusBadge status={connection ? "CONFIGURED" : descriptor.ui_status} />
              <StatusBadge status={descriptor.verification_status} />
            </div>
          </div>

          <p style={{ color: "#475569", fontSize: "0.9rem" }}>
            {descriptor.docs_note}
          </p>

          <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem", marginBottom: "1rem" }}>
            {descriptor.capabilities.map((cap) => (
              <span
                key={cap}
                style={{
                  fontSize: "0.75rem",
                  padding: "0.18rem 0.55rem",
                  background: "#f8fafc",
                  border: "1px solid #cbd5e1",
                  borderRadius: 6,
                }}
              >
                {cap}
              </span>
            ))}
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "0.75rem",
              marginBottom: "1rem",
            }}
          >
            <label style={{ fontSize: "0.84rem" }}>
              Instance / Subdomain URL
              <input
                value={instanceUrl}
                onChange={(e) => setInstanceUrl(e.target.value)}
                placeholder="https://acme.zendesk.com"
                style={{ display: "block", width: "100%", padding: "0.4rem", marginTop: 4 }}
              />
            </label>
            <label style={{ fontSize: "0.84rem" }}>
              Region
              <input
                value={region}
                onChange={(e) => setRegion(e.target.value)}
                placeholder="us"
                style={{ display: "block", width: "100%", padding: "0.4rem", marginTop: 4 }}
              />
            </label>
          </div>

          {descriptor.auth_modes.includes("oauth2") && (
            <div style={{ marginBottom: "1rem" }}>
              <ConnectButton
                provider={slug}
                instanceUrl={instanceUrl}
                region={region}
                extraParams={orgId ? { org_id: orgId } : {}}
                onConnected={(conn) => {
                  setConnection(conn);
                  setStatusMessage("OAuth 2.0 connection established.");
                }}
              />
            </div>
          )}

          {descriptor.auth_modes.includes("api_key") && (
            <form onSubmit={handleApiKeySave} style={{ marginTop: "0.75rem" }}>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem" }}>
                <label style={{ fontSize: "0.84rem" }}>
                  API Key
                  <input
                    type="password"
                    required
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    style={{ display: "block", width: "100%", padding: "0.4rem", marginTop: 4 }}
                  />
                </label>
                <label style={{ fontSize: "0.84rem" }}>
                  Webhook Signing Secret (optional)
                  <input
                    type="password"
                    value={webhookSecret}
                    onChange={(e) => setWebhookSecret(e.target.value)}
                    style={{ display: "block", width: "100%", padding: "0.4rem", marginTop: 4 }}
                  />
                </label>
              </div>
              {slug === "zoho_desk" && (
                <label style={{ display: "block", fontSize: "0.84rem", marginTop: "0.5rem" }}>
                  Zoho Desk Organization ID
                  <input
                    value={orgId}
                    onChange={(e) => setOrgId(e.target.value)}
                    style={{ display: "block", width: "100%", padding: "0.4rem", marginTop: 4 }}
                  />
                </label>
              )}
              <button
                type="submit"
                style={{
                  marginTop: "0.75rem",
                  padding: "0.45rem 0.9rem",
                  borderRadius: 6,
                  border: "1px solid #0f172a",
                  background: "#0f172a",
                  color: "#ffffff",
                  cursor: "pointer",
                }}
              >
                Save API Key & Test
              </button>
            </form>
          )}

          {connection && (
            <div
              style={{
                marginTop: "1.25rem",
                paddingTop: "1rem",
                borderTop: "1px solid #e2e8f0",
                display: "flex",
                flexWrap: "wrap",
                gap: "0.6rem",
                alignItems: "center",
              }}
            >
              <button type="button" onClick={handleTest}>
                Test Connection
              </button>
              {descriptor.category !== "collab" && (
                <>
                  <button type="button" onClick={handleTriggerSync}>
                    Run Incremental Sync
                  </button>
                  <Link href={`/integrations-v2/${slug}/mapping`}>
                    Field Mapping Editor →
                  </Link>
                </>
              )}
              <button
                type="button"
                onClick={handleDisconnect}
                style={{ color: "#991b1b", marginLeft: "auto" }}
              >
                Disconnect
              </button>
            </div>
          )}

          {connection?.last_error && (
            <div
              style={{
                marginTop: "0.75rem",
                padding: "0.6rem",
                background: "#fef2f2",
                color: "#991b1b",
                borderRadius: 6,
                fontSize: "0.82rem",
              }}
            >
              Last error: {connection.last_error}
            </div>
          )}
        </section>
      )}

      {connection && (
        <section>
          <h2 style={{ fontSize: "1.1rem" }}>Recent Sync Runs</h2>
          <SyncRunTable runs={runs} onRetry={() => void handleTriggerSync()} />
        </section>
      )}
    </main>
  );
}
