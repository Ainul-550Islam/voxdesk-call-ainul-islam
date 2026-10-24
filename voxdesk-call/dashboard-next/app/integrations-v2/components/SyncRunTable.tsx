"use client";

import React, { useState } from "react";
import type { SyncRunRow } from "../lib/api";

export interface SyncRunTableProps {
  runs: SyncRunRow[];
  pageSize?: number;
  onRetry?: (run: SyncRunRow) => void;
}

export function SyncRunTable({
  runs,
  pageSize = 10,
  onRetry,
}: SyncRunTableProps) {
  const [page, setPage] = useState(0);
  const totalPages = Math.max(1, Math.ceil(runs.length / pageSize));
  const slice = runs.slice(page * pageSize, (page + 1) * pageSize);

  if (runs.length === 0) {
    return (
      <div
        style={{
          padding: "1rem",
          border: "1px solid #e2e8f0",
          borderRadius: 8,
          color: "#64748b",
          fontSize: "0.85rem",
        }}
      >
        No sync runs recorded yet.
      </div>
    );
  }

  return (
    <div>
      <table
        data-testid="sync-run-table"
        style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.84rem" }}
      >
        <thead>
          <tr style={{ textAlign: "left", borderBottom: "1px solid #cbd5e1" }}>
            <th style={{ padding: "0.45rem" }}>Started</th>
            <th style={{ padding: "0.45rem" }}>Entity</th>
            <th style={{ padding: "0.45rem" }}>Status</th>
            <th style={{ padding: "0.45rem" }}>Seen / Upserted / Failed</th>
            <th style={{ padding: "0.45rem" }}>Cursor</th>
            <th style={{ padding: "0.45rem" }}>Error</th>
            <th style={{ padding: "0.45rem" }} />
          </tr>
        </thead>
        <tbody>
          {slice.map((r) => (
            <tr key={r.id} style={{ borderBottom: "1px solid #f1f5f9" }}>
              <td style={{ padding: "0.45rem" }}>
                {r.started_at ? new Date(r.started_at).toLocaleString() : "—"}
              </td>
              <td style={{ padding: "0.45rem" }}>{r.entity_type}</td>
              <td style={{ padding: "0.45rem", fontWeight: 600 }}>{r.status}</td>
              <td style={{ padding: "0.45rem" }}>
                {r.records_seen} / {r.records_upserted} / {r.records_failed}
              </td>
              <td style={{ padding: "0.45rem", fontFamily: "monospace" }}>
                {r.cursor_after || "—"}
              </td>
              <td style={{ padding: "0.45rem", color: "#991b1b" }}>
                {r.error_summary || ""}
              </td>
              <td style={{ padding: "0.45rem" }}>
                {onRetry && (
                  <button
                    type="button"
                    onClick={() => onRetry(r)}
                    style={{
                      padding: "0.25rem 0.55rem",
                      borderRadius: 4,
                      border: "1px solid #cbd5e1",
                      background: "#ffffff",
                      cursor: "pointer",
                    }}
                  >
                    Retry
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {totalPages > 1 && (
        <div
          style={{
            display: "flex",
            gap: "0.5rem",
            alignItems: "center",
            marginTop: "0.6rem",
            fontSize: "0.8rem",
          }}
        >
          <button
            type="button"
            disabled={page === 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
          >
            Prev
          </button>
          <span>
            Page {page + 1} of {totalPages}
          </span>
          <button
            type="button"
            disabled={page + 1 >= totalPages}
            onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
