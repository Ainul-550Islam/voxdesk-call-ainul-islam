"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { int2Api, type SyncRunRow } from "../lib/api";
import { SyncRunTable } from "../components/SyncRunTable";

export default function SyncRunsHistoryPage() {
  const [runs, setRuns] = useState<SyncRunRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [statusMessage, setStatusMessage] = useState("");

  const loadRuns = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await int2Api.listSyncRuns();
      setRuns(res.sync_runs);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load sync runs");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadRuns();
  }, [loadRuns]);

  const handleRetry = async (run: SyncRunRow) => {
    setError("");
    setStatusMessage("");
    const entity =
      run.entity_type === "Ticket"
        ? "Ticket"
        : run.entity_type === "Lead"
          ? "Lead"
          : "Contact";
    try {
      const res = await int2Api.triggerSync(run.connection_id, entity);
      setRuns((prev) => [res.sync_run, ...prev]);
      setStatusMessage(`Retried sync (${res.sync_run.status}).`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Retry failed");
    }
  };

  return (
    <main style={{ maxWidth: 1080, margin: "0 auto", padding: "1.75rem" }}>
      <div style={{ marginBottom: "1rem" }}>
        <Link href="/integrations-v2">← Back to Integrations Catalog</Link>
      </div>
      <h1 style={{ marginTop: 0 }}>Incremental Sync Run History</h1>

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

      {loading ? (
        <p>Loading sync runs…</p>
      ) : (
        <SyncRunTable runs={runs} onRetry={handleRetry} />
      )}
    </main>
  );
}
