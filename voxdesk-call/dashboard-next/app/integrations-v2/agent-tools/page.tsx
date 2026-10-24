"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import {
  int2Api,
  type AgentToolSpec,
  type ConnectionMasked,
  type ToolEnablementItem,
} from "../lib/api";

export default function AgentToolsV2Page() {
  const [specs, setSpecs] = useState<AgentToolSpec[]>([]);
  const [enablements, setEnablements] = useState<ToolEnablementItem[]>([]);
  const [connections, setConnections] = useState<ConnectionMasked[]>([]);
  const [dryRunTool, setDryRunTool] = useState("crm_lookup_contact");
  const [dryRunArgs, setDryRunArgs] = useState('{"phone": "+14155552671"}');
  const [dryRunOutput, setDryRunOutput] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([int2Api.getAgentToolEnablements(), int2Api.listConnections()])
      .then(([toolRes, connRes]) => {
        setSpecs(toolRes.specs);
        setEnablements(toolRes.enablements);
        setConnections(connRes.connections);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Failed to load agent tools");
      });
  }, []);

  const handleToggle = async (
    spec: AgentToolSpec,
    connectionId: string,
    enabled: boolean
  ) => {
    setError("");
    try {
      const res = await int2Api.upsertToolEnablement({
        tool_name: spec.name,
        connection_id: connectionId,
        enabled,
      });
      setEnablements((prev) => [
        res.enablement,
        ...prev.filter((e) => e.tool_name !== spec.name),
      ]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update tool enablement");
    }
  };

  const handleDryRun = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setDryRunOutput("");
    try {
      const parsed = JSON.parse(dryRunArgs) as Record<string, unknown>;
      const res = await int2Api.dryRunTool(dryRunTool, { arguments: parsed });
      setDryRunOutput(JSON.stringify(res, null, 2));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Dry-run failed");
    }
  };

  return (
    <main style={{ maxWidth: 1020, margin: "0 auto", padding: "1.75rem" }}>
      <div style={{ marginBottom: "1rem" }}>
        <Link href="/integrations-v2">← Back to Integrations Catalog</Link>
      </div>
      <h1 style={{ marginTop: 0 }}>Voice Agent Integration Tools</h1>

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

      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem" }}>
        <thead>
          <tr style={{ textAlign: "left", borderBottom: "1px solid #cbd5e1" }}>
            <th style={{ padding: "0.45rem" }}>Tool</th>
            <th style={{ padding: "0.45rem" }}>Category</th>
            <th style={{ padding: "0.45rem" }}>Bound Connection</th>
            <th style={{ padding: "0.45rem" }}>Enabled</th>
          </tr>
        </thead>
        <tbody>
          {specs.map((spec) => {
            const existing = enablements.find((e) => e.tool_name === spec.name);
            const matchingConns = connections.filter(
              (c) => c.category === spec.category
            );
            const selectedConn =
              existing?.connection_id || matchingConns[0]?.id || "";
            return (
              <tr key={spec.name} style={{ borderBottom: "1px solid #f1f5f9" }}>
                <td style={{ padding: "0.45rem" }}>
                  <strong>{spec.name}</strong>
                  <div style={{ color: "#64748b", fontSize: "0.78rem" }}>
                    {spec.description}
                  </div>
                </td>
                <td style={{ padding: "0.45rem" }}>{spec.category}</td>
                <td style={{ padding: "0.45rem" }}>
                  <select
                    value={selectedConn}
                    disabled={matchingConns.length === 0}
                    onChange={(e) =>
                      void handleToggle(
                        spec,
                        e.target.value,
                        Boolean(existing?.enabled)
                      )
                    }
                  >
                    {matchingConns.length === 0 ? (
                      <option value="">No {spec.category} connection</option>
                    ) : (
                      matchingConns.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.display_name} ({c.provider})
                        </option>
                      ))
                    )}
                  </select>
                </td>
                <td style={{ padding: "0.45rem" }}>
                  <input
                    type="checkbox"
                    disabled={!selectedConn}
                    checked={Boolean(existing?.enabled)}
                    onChange={(e) =>
                      void handleToggle(spec, selectedConn, e.target.checked)
                    }
                  />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <section style={{ marginTop: "2rem" }}>
        <h2 style={{ fontSize: "1.1rem" }}>Dry-Run Tool Validator (Zero Vendor Calls)</h2>
        <form onSubmit={handleDryRun}>
          <div style={{ display: "flex", gap: "0.75rem", marginBottom: "0.6rem" }}>
            <select
              value={dryRunTool}
              onChange={(e) => setDryRunTool(e.target.value)}
            >
              {specs.map((s) => (
                <option key={s.name} value={s.name}>
                  {s.name}
                </option>
              ))}
            </select>
            <button type="submit">Validate Arguments & RBAC</button>
          </div>
          <textarea
            rows={4}
            value={dryRunArgs}
            onChange={(e) => setDryRunArgs(e.target.value)}
            style={{ width: "100%", fontFamily: "monospace", padding: "0.5rem" }}
          />
        </form>
        {dryRunOutput && (
          <pre
            style={{
              marginTop: "0.75rem",
              background: "#0f172a",
              color: "#f8fafc",
              padding: "0.75rem",
              borderRadius: 8,
              fontSize: "0.8rem",
            }}
          >
            {dryRunOutput}
          </pre>
        )}
      </section>
    </main>
  );
}
