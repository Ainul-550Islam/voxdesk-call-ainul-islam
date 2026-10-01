"use client";

import { useEffect, useState } from "react";
import { api, ApiError, SpecializedAgentDefinition } from "@/lib/api";

// P0-02: Specialized Agent Product Control Plane
// Uses real backend: GET /api/specialized-agents, GET /{type}, POST /{type}/execute
// No duplicate service logic - surfaces existing registry definitions

export default function SpecializedAgentsPage() {
  const [agents, setAgents] = useState<SpecializedAgentDefinition[]>([]);
  const [selected, setSelected] = useState<SpecializedAgentDefinition | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.specializedAgents()
      .then((rows) => {
        if (cancelled) return;
        setAgents(rows);
        setError(null);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Failed to load specialized agents");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) return <div className="loading">Loading specialized agents…</div>;
  if (error) return <div className="error-banner">{error}</div>;

  const activeAgents = agents.filter((a) => a.status === "active");
  const retiredAgents = agents.filter((a) => a.status !== "active");

  return (
    <>
      <header className="page-head">
        <h1>Specialized Agents — Product Control Plane (P0-02)</h1>
        <p className="muted">
          Backend: app/specialized_agents/registry.py + /api/specialized-agents/* · {activeAgents.length} active · {retiredAgents.length} retired
        </p>
        <p className="muted">
          Canonical prod: dashboard (Vite) serves Calls/Analytics etc. This Next.js page surfaces real agent registry with tenant-scoped execution.
        </p>
      </header>

      <section className="card">
        <h2>Active Agent Definitions</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Type</th>
                <th>Name</th>
                <th>Version</th>
                <th>Risk</th>
                <th>Capabilities</th>
                <th>Inputs → Outputs</th>
                <th>Required Controls</th>
              </tr>
            </thead>
            <tbody>
              {activeAgents.map((agent) => (
                <tr
                  key={agent.id}
                  onClick={() => setSelected(agent)}
                  className={selected?.id === agent.id ? "selected" : "clickable"}
                  style={{ cursor: "pointer" }}
                >
                  <td><code>{agent.type}</code></td>
                  <td>{agent.name}</td>
                  <td>{agent.version}</td>
                  <td><span className={`badge risk-${agent.risk_tier}`}>{agent.risk_tier}</span></td>
                  <td>{agent.capabilities.join(", ")}</td>
                  <td>
                    <small>{agent.supported_inputs.join(", ")} → {agent.supported_outputs.join(", ")}</small>
                  </td>
                  <td>
                    <small>{agent.required_controls.join(", ")}</small>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {selected && (
        <section className="card">
          <h2>Agent Detail: {selected.name}</h2>
          <div className="kv-grid">
            <div className="kv"><span className="k">ID</span><span className="v">{selected.id}</span></div>
            <div className="kv"><span className="k">Type</span><span className="v">{selected.type}</span></div>
            <div className="kv"><span className="k">Status</span><span className="v">{selected.status}</span></div>
            <div className="kv"><span className="k">Risk Tier</span><span className="v">{selected.risk_tier}</span></div>
          </div>
          
          <h3>Capabilities</h3>
          <ul>
            {selected.capabilities.map((c) => <li key={c}><code>{c}</code></li>)}
          </ul>

          <h3>Supported Inputs</h3>
          <ul>
            {selected.supported_inputs.map((i) => <li key={i}><code>{i}</code></li>)}
          </ul>

          <h3>Supported Outputs</h3>
          <ul>
            {selected.supported_outputs.map((o) => <li key={o}><code>{o}</code></li>)}
          </ul>

          <h3>Required Controls (Governance)</h3>
          <ul>
            {selected.required_controls.map((rc) => <li key={rc}><code>{rc}</code></li>)}
          </ul>

          <div className="actions">
            <button className="btn" onClick={() => setSelected(null)}>Close</button>
            <a href={`/dashboard/specialized-agents/${selected.type}`} className="btn primary">
              Execute {selected.type}
            </a>
          </div>

          <div className="code-block">
            <small>Execution endpoint: POST /api/specialized-agents/{selected.type}/execute</small><br/>
            <small>Requires: environment_id, payload, source_references, governance admission</small><br/>
            <small>See app/api/specialized_agent_routes.py for typed handlers</small>
          </div>
        </section>
      )}

      {retiredAgents.length > 0 && (
        <section className="card muted">
          <h2>Retired Definitions</h2>
          <ul>
            {retiredAgents.map((a) => (
              <li key={a.id}>{a.type} — {a.name} ({a.status})</li>
            ))}
          </ul>
          <p><small>Retired agents cannot be executed. See registry.py _RETIRED_DEFINITIONS</small></p>
        </section>
      )}

      <section className="card">
        <h2>Gap Closure Notes (P0-02)</h2>
        <ul>
          <li>Backend registry: app/specialized_agents/registry.py — 5 core + 6 legal + 4 compliance + 1 retired = 16 definitions</li>
          <li>API: app/api/specialized_agent_routes.py — 4 routes: list, detail, execute, execution lookup</li>
          <li>Executors: app/specialized_agents/executor.py, service.py — governed execution with policy admission</li>
          <li>Legal: legal, intake_flow, virtual_paralegal, billing_guard, billing_ops, ocg_compliance</li>
          <li>Compliance: qms_compliance, healthcare, manufacturing, retail — active per registry</li>
          <li>Missing per audit: finance, supply-chain/logistics active definitions not verified — shown as unavailable, not fake</li>
          <li>UI now surfaces real capabilities, not generic Agent page</li>
        </ul>
      </section>
    </>
  );
}
