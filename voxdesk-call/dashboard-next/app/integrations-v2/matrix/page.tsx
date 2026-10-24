"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { int2Api, type MatrixProviderRow } from "../lib/api";
import { StatusBadge } from "../components/StatusBadge";

export default function CapabilityMatrixPage() {
  const [rows, setRows] = useState<MatrixProviderRow[]>([]);
  const [summary, setSummary] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    int2Api
      .getMatrix()
      .then((res) => {
        if (!active) return;
        setRows(res.providers);
        setSummary(res.summary);
      })
      .catch((err) => {
        if (active) {
          setError(err instanceof Error ? err.message : "Failed to load matrix");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <main style={{ maxWidth: 1120, margin: "0 auto", padding: "1.75rem" }}>
      <div style={{ marginBottom: "1rem" }}>
        <Link href="/integrations-v2">← Back to Integrations Catalog</Link>
      </div>
      <h1 style={{ marginTop: 0 }}>Capability & Verification Matrix</h1>
      <p style={{ color: "#475569", fontSize: "0.88rem" }}>
        LIVE_VERIFIED is displayed strictly when a passing live sandbox test artifact and
        credentials exist. Unconfigured providers report NOT_CONFIGURED.
      </p>

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

      {!loading && !error && (
        <>
          <div style={{ display: "flex", gap: "1rem", marginBottom: "1.25rem" }}>
            {Object.entries(summary).map(([tier, count]) => (
              <div
                key={tier}
                style={{
                  border: "1px solid #e2e8f0",
                  borderRadius: 8,
                  padding: "0.6rem 0.9rem",
                }}
              >
                <StatusBadge status={tier} />{" "}
                <strong style={{ marginLeft: 6 }}>{count}</strong>
              </div>
            ))}
          </div>

          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid #cbd5e1" }}>
                <th style={{ padding: "0.5rem" }}>Provider</th>
                <th style={{ padding: "0.5rem" }}>Category</th>
                <th style={{ padding: "0.5rem" }}>Verification Tier</th>
                <th style={{ padding: "0.5rem" }}>Last Verified</th>
                <th style={{ padding: "0.5rem" }}>Capabilities</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.provider} style={{ borderBottom: "1px solid #f1f5f9" }}>
                  <td style={{ padding: "0.5rem", fontWeight: 600 }}>
                    {r.display_name}
                  </td>
                  <td style={{ padding: "0.5rem" }}>{r.category}</td>
                  <td style={{ padding: "0.5rem" }}>
                    <StatusBadge status={r.verification_status} />
                  </td>
                  <td style={{ padding: "0.5rem" }}>
                    {r.last_verified_at
                      ? new Date(r.last_verified_at).toLocaleString()
                      : "—"}
                  </td>
                  <td style={{ padding: "0.5rem" }}>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: "0.3rem" }}>
                      {Object.keys(r.capabilities).map((cap) => (
                        <span
                          key={cap}
                          style={{
                            fontSize: "0.72rem",
                            padding: "0.1rem 0.4rem",
                            border: "1px solid #e2e8f0",
                            borderRadius: 4,
                          }}
                        >
                          {cap}
                        </span>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </main>
  );
}
