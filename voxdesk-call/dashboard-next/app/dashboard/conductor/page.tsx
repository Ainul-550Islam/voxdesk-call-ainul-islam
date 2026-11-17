// File: dashboard-next/app/dashboard/conductor/page.tsx — conductor console
"use client";

import { useEffect, useState } from "react";
import { ApiError, request } from "@/lib/api";
import ConductorPanel from "@/components/conductor/ConductorPanel";

/**
 * Conductor console (PART 15, P15-05).
 *
 * A thin shell: pick the agent the conductor may work on, then hand over to
 * `ConductorPanel`. The agent list comes from `GET /api/agents`, which is the
 * same registry the rest of the dashboard reads — no invented agents, and no
 * default selection, because a conductor pointed at the wrong agent proposes
 * changes to the wrong agent.
 */

interface AgentRow {
  id?: string;
  name?: string;
  status?: string;
}

export default function ConductorPage() {
  const [agents, setAgents] = useState<AgentRow[]>([]);
  const [agentId, setAgentId] = useState("");
  const [agentKind, setAgentKind] = useState<"voice" | "chat">("voice");
  const [baseVersion, setBaseVersion] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    request<AgentRow[] | { agents?: AgentRow[] }>("/api/agents")
      .then((payload) => {
        if (cancelled) return;
        const rows = Array.isArray(payload) ? payload : payload?.agents ?? [];
        setAgents(rows);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Could not load the agent list.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const parsedBase = Number.parseInt(baseVersion, 10);

  return (
    <div data-testid="conductor-page">
      <header className="page-head">
        <div>
          <h1>Conductor</h1>
          <p className="muted">
            Describe a change in plain language and the conductor proposes per-field edits with
            risk, validation and simulation state. It never publishes on its own: applying writes a
            new draft version, and publishing stays a separate human action.
          </p>
        </div>
      </header>

      {error ? (
        <div className="error-banner" role="alert">
          {error}
        </div>
      ) : null}

      <section className="card">
        <h2>Target</h2>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
            gap: "0.75rem",
          }}
        >
          <label>
            Agent
            <select
              value={agentId}
              onChange={(event) => setAgentId(event.target.value)}
              data-testid="conductor-agent-select"
            >
              <option value="">{loading ? "Loading agents…" : "Choose an agent"}</option>
              {agents.map((agent, index) => {
                const id = agent.id ?? "";
                return (
                  <option key={id || index} value={id}>
                    {agent.name ? `${agent.name} (${id.slice(0, 8)})` : id}
                    {agent.status ? ` — ${agent.status}` : ""}
                  </option>
                );
              })}
            </select>
          </label>
          <label>
            Agent kind
            <select
              value={agentKind}
              onChange={(event) => setAgentKind(event.target.value as "voice" | "chat")}
            >
              <option value="voice">voice</option>
              <option value="chat">chat</option>
            </select>
          </label>
          <label>
            Base version (optional)
            <input
              type="number"
              min={1}
              value={baseVersion}
              onChange={(event) => setBaseVersion(event.target.value)}
              placeholder="current published version"
            />
            <small className="muted">
              Pinning the base makes a stale-context apply fail loudly instead of editing the wrong
              version.
            </small>
          </label>
        </div>
        {!loading && agents.length === 0 ? (
          <p className="muted">No agents in this workspace yet. Create one before using the conductor.</p>
        ) : null}
      </section>

      <ConductorPanel
        agentId={agentId}
        agentKind={agentKind}
        baseVersionNumber={Number.isFinite(parsedBase) && parsedBase >= 1 ? parsedBase : undefined}
      />
    </div>
  );
}
