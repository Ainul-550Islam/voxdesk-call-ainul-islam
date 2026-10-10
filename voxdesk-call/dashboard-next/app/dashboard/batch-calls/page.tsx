"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, request } from "@/lib/api";
import { getToken } from "@/lib/auth";

interface BatchCallRecord {
  id: string;
  name: string;
  agent_id: string | null;
  status: string;
  total_recipients: number;
  completed_count: number;
  failed_count: number;
  voicemail_count: number;
  no_answer_count: number;
  busy_count: number;
  skipped_count: number;
  max_concurrency: number;
  max_attempts: number;
  created_at: string | null;
}

interface BatchRecipientRecord {
  id: string;
  phone_number: string;
  status: string;
  attempt_count: number;
  dynamic_variables: Record<string, unknown>;
  outcome: string | null;
  last_error: string | null;
}

interface ParsedRecipient {
  phone_number: string;
  dynamic_variables: Record<string, string>;
}

function parseCsvToRecipients(csvText: string): ParsedRecipient[] {
  const lines = csvText
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
  if (lines.length === 0) return [];

  const headers = lines[0].split(",").map((h) => h.trim());
  const hasHeader =
    headers[0].toLowerCase().includes("phone") ||
    headers[0].toLowerCase() === "number" ||
    headers[0].toLowerCase() === "to";

  const startIdx = hasHeader ? 1 : 0;
  const out: ParsedRecipient[] = [];

  for (let i = startIdx; i < lines.length; i++) {
    const cols = lines[i].split(",").map((c) => c.trim());
    const phone = cols[0];
    if (!phone) continue;
    const vars: Record<string, string> = {};
    if (hasHeader) {
      for (let j = 1; j < headers.length; j++) {
        if (headers[j] && cols[j] !== undefined) {
          vars[headers[j]] = cols[j];
        }
      }
    }
    out.push({ phone_number: phone, dynamic_variables: vars });
  }
  return out;
}

export default function BatchCallsPage() {
  const [batches, setBatches] = useState<BatchCallRecord[]>([]);
  const [selectedBatch, setSelectedBatch] = useState<BatchCallRecord | null>(null);
  const [recipients, setRecipients] = useState<BatchRecipientRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Create form state
  const [batchName, setBatchName] = useState("");
  const [agentId, setAgentId] = useState("");
  const [maxConcurrency, setMaxConcurrency] = useState(5);
  const [maxAttempts, setMaxAttempts] = useState(3);
  const [csvContent, setCsvContent] = useState(
    "phone_number,customer_name,appointment_time\n+14155550101,Alice,2:00 PM\n+14155550102,Bob,3:30 PM",
  );

  const loadBatches = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await request<{ batch_calls: BatchCallRecord[] }>(
        "/api/batch-calls",
      );
      setBatches(res.batch_calls);
      if (res.batch_calls.length > 0 && !selectedBatch) {
        setSelectedBatch(res.batch_calls[0]);
      }
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to load batch calls",
      );
    } finally {
      setLoading(false);
    }
  }, [selectedBatch]);

  useEffect(() => {
    if (!getToken()) {
      setLoading(false);
      return;
    }
    void loadBatches();
  }, [loadBatches]);

  useEffect(() => {
    if (!selectedBatch || !getToken()) return;
    let cancelled = false;
    request<{ recipients: BatchRecipientRecord[] }>(
      `/api/batch-calls/${encodeURIComponent(selectedBatch.id)}/recipients`,
    )
      .then((res) => {
        if (!cancelled) setRecipients(res.recipients);
      })
      .catch(() => {
        if (!cancelled) setRecipients([]);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedBatch]);

  function handleCsvFileUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === "string") {
        setCsvContent(reader.result);
      }
    };
    reader.readAsText(file);
  }

  async function handleCreateBatch(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const parsed = parseCsvToRecipients(csvContent);
    if (!batchName.trim()) {
      setError("Batch name is required.");
      return;
    }
    if (parsed.length === 0) {
      setError("At least one recipient phone number is required.");
      return;
    }
    try {
      const created = await request<BatchCallRecord>("/api/batch-calls", {
        method: "POST",
        body: JSON.stringify({
          name: batchName.trim(),
          agent_id: agentId.trim() || null,
          max_concurrency: maxConcurrency,
          max_attempts: maxAttempts,
          recipients: parsed,
        }),
      });
      setBatchName("");
      setBatches((prev) => [created, ...prev]);
      setSelectedBatch(created);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to create batch call",
      );
    }
  }

  async function handleBatchAction(
    batchId: string,
    action: "pause" | "resume" | "cancel",
  ) {
    setError(null);
    try {
      const updated = await request<BatchCallRecord>(
        `/api/batch-calls/${encodeURIComponent(batchId)}/${action}`,
        { method: "POST" },
      );
      setBatches((prev) => prev.map((b) => (b.id === batchId ? updated : b)));
      if (selectedBatch?.id === batchId) {
        setSelectedBatch(updated);
      }
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : `Failed to ${action} batch call`,
      );
    }
  }

  const parsedPreview = parseCsvToRecipients(csvContent);

  return (
    <div data-testid="batch-calls-page">
      <header className="page-head">
        <div>
          <h1>Batch Outbound Calls</h1>
          <p className="muted">
            Upload CSV recipient lists with dynamic variables, monitor live progress, and pause/resume/cancel campaigns.
          </p>
        </div>
      </header>

      {error ? (
        <div className="error-banner" role="alert">
          {error}
        </div>
      ) : null}

      <section className="card" data-testid="create-batch-card">
        <h2>Create Batch Call Campaign</h2>
        <form onSubmit={handleCreateBatch} style={{ display: "grid", gap: "0.75rem" }}>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
              gap: "0.75rem",
            }}
          >
            <label>
              Batch Name
              <input
                type="text"
                data-testid="batch-name-input"
                placeholder="October Appointment Reminders"
                value={batchName}
                onChange={(e) => setBatchName(e.target.value)}
              />
            </label>
            <label>
              Agent ID (optional)
              <input
                type="text"
                placeholder="Agent UUID"
                value={agentId}
                onChange={(e) => setAgentId(e.target.value)}
              />
            </label>
            <label>
              Max Concurrency
              <input
                type="number"
                min={1}
                max={100}
                value={maxConcurrency}
                onChange={(e) => setMaxConcurrency(Number(e.target.value))}
              />
            </label>
            <label>
              Max Attempts
              <input
                type="number"
                min={1}
                max={10}
                value={maxAttempts}
                onChange={(e) => setMaxAttempts(Number(e.target.value))}
              />
            </label>
          </div>

          <label>
            Upload CSV File (optional)
            <input
              type="file"
              accept=".csv,text/csv"
              data-testid="batch-csv-file-input"
              onChange={handleCsvFileUpload}
            />
          </label>

          <label>
            CSV Recipients &amp; Dynamic Variables (first column: phone_number, remaining columns: variables)
            <textarea
              rows={4}
              data-testid="batch-csv-textarea"
              value={csvContent}
              onChange={(e) => setCsvContent(e.target.value)}
            />
          </label>

          <p className="muted" data-testid="parsed-recipients-preview">
            Parsed {parsedPreview.length} recipient(s) with variables:{" "}
            {parsedPreview[0]
              ? Object.keys(parsedPreview[0].dynamic_variables).join(", ") || "none"
              : "none"}
          </p>

          <div>
            <button
              type="submit"
              className="btn primary"
              data-testid="create-batch-submit-btn"
            >
              Launch Batch Campaign
            </button>
          </div>
        </form>
      </section>

      <section className="card" data-testid="batch-list-card">
        <h2>Batch Campaigns ({batches.length})</h2>
        {loading ? (
          <div className="loading">Loading batch campaigns…</div>
        ) : batches.length === 0 ? (
          <div className="empty">No batch call campaigns created yet.</div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Status</th>
                <th>Progress</th>
                <th>Completed</th>
                <th>Failed / Voicemail</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {batches.map((b) => {
                const processed =
                  b.completed_count +
                  b.failed_count +
                  b.voicemail_count +
                  b.no_answer_count +
                  b.busy_count +
                  b.skipped_count;
                const pct =
                  b.total_recipients > 0
                    ? Math.round((processed / b.total_recipients) * 100)
                    : 0;
                return (
                  <tr key={b.id}>
                    <td>
                      <button
                        type="button"
                        className="btn"
                        onClick={() => setSelectedBatch(b)}
                      >
                        {b.name}
                      </button>
                    </td>
                    <td>
                      <span className={`badge ${b.status}`}>{b.status}</span>
                    </td>
                    <td>
                      {processed} / {b.total_recipients} ({pct}%)
                    </td>
                    <td>{b.completed_count}</td>
                    <td>
                      {b.failed_count} failed · {b.voicemail_count} voicemail
                    </td>
                    <td>
                      <div style={{ display: "flex", gap: "0.35rem" }}>
                        <button
                          type="button"
                          className="btn"
                          data-testid={`pause-batch-${b.id}`}
                          onClick={() => handleBatchAction(b.id, "pause")}
                        >
                          Pause
                        </button>
                        <button
                          type="button"
                          className="btn"
                          data-testid={`resume-batch-${b.id}`}
                          onClick={() => handleBatchAction(b.id, "resume")}
                        >
                          Resume
                        </button>
                        <button
                          type="button"
                          className="btn"
                          data-testid={`cancel-batch-${b.id}`}
                          onClick={() => handleBatchAction(b.id, "cancel")}
                        >
                          Cancel
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>

      {selectedBatch ? (
        <section className="card" data-testid="batch-recipients-card">
          <h2>
            Recipient Outcomes — {selectedBatch.name} ({recipients.length})
          </h2>
          {recipients.length === 0 ? (
            <div className="empty">No recipients loaded.</div>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Phone</th>
                  <th>Status</th>
                  <th>Attempts</th>
                  <th>Variables</th>
                  <th>Outcome / Error</th>
                </tr>
              </thead>
              <tbody>
                {recipients.map((r) => (
                  <tr key={r.id}>
                    <td>{r.phone_number}</td>
                    <td>{r.status}</td>
                    <td>{r.attempt_count}</td>
                    <td>
                      <code>{JSON.stringify(r.dynamic_variables)}</code>
                    </td>
                    <td>{r.outcome ?? r.last_error ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      ) : null}
    </div>
  );
}
