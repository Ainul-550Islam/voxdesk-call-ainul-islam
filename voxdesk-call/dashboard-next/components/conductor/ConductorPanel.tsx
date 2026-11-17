// File: dashboard-next/components/conductor/ConductorPanel.tsx — agent conductor: propose, review, apply
"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, request } from "@/lib/api";

/**
 * Conductor panel (PART 15, P15-05).
 *
 * The conductor is a *proposal* machine, not an autopilot. It reads the agent's
 * scoped context, turns a natural-language request into a set of changes, and
 * then stops: nothing reaches the agent until a human approves and applies. This
 * component follows the same rule and shows the three states the backend keeps
 * separate — validated, simulated, applied — because "the change was generated"
 * and "the change is live" are different claims.
 *
 * Endpoints (app/api/conductor_routes.py, prefix `/api/v1/conductor`):
 *   GET  /context?agent_id=&agent_kind=        scoped snapshot the model saw
 *   GET  /proposals?agent_id=                  proposals for this agent
 *   POST /proposals                            submit a request
 *   GET  /proposals/{id}/changes               one row per proposed change
 *   POST /proposals/{id}/validate              re-validate
 *   POST /proposals/{id}/simulate              dry simulation
 *   POST /proposals/{id}/approve               approve the proposal
 *   POST /proposals/{id}/apply                 write a new agent version
 */

export interface ConductorChange {
  id: string;
  sequence: number;
  section: string;
  path: string;
  operation: string;
  reason: string;
  risk_level: string;
  validation_state: string;
  validation_messages: string[];
  simulation_state: string;
  approval_state: string;
}

export interface ConductorProposal {
  id: string;
  session_id: string;
  agent_id: string;
  agent_kind: string;
  base_version_number: number;
  base_config_hash: string;
  request_text: string;
  summary: string;
  rationale: string;
  status: string;
  validation_status: string;
  simulation_status: string;
  risk_summary: Record<string, unknown>;
  resulting_version_number: number | null;
  is_mock_provider: boolean;
  provider: string;
  model: string;
  created_at: string;
}

interface ApplyResponse {
  proposal: ConductorProposal;
  applied_change_ids: string[];
  skipped_change_ids: string[];
  base_version_number: number;
  resulting_version_number: number;
  production_published: boolean;
}

interface ContextResponse {
  agent_id?: string;
  agent_kind?: string;
  version_number?: number;
  [key: string]: unknown;
}

const RISK_BADGE: Record<string, string> = {
  low: "badge completed",
  medium: "badge paused",
  moderate: "badge paused",
  high: "badge failed",
  critical: "badge failed",
};

const STATUS_BADGE: Record<string, string> = {
  applied: "badge completed",
  approved: "badge completed",
  validated: "badge in_progress",
  pending: "badge draft",
  draft: "badge draft",
  rejected: "badge failed",
  failed: "badge failed",
  stale: "badge failed",
};

function badge(map: Record<string, string>, value: string): string {
  return map[value?.toLowerCase()] ?? "badge";
}

export interface ConductorPanelProps {
  /** Agent the conductor may propose changes for. Empty disables the panel. */
  agentId: string;
  agentKind?: "voice" | "chat";
  /** Current published version number, used as the base for a new request. */
  baseVersionNumber?: number;
}

export default function ConductorPanel({
  agentId,
  agentKind = "voice",
  baseVersionNumber,
}: ConductorPanelProps) {
  const [requestText, setRequestText] = useState("");
  const [proposals, setProposals] = useState<ConductorProposal[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [changes, setChanges] = useState<ConductorChange[]>([]);
  const [context, setContext] = useState<ContextResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const selected = proposals.find((p) => p.id === selectedId) ?? null;

  const loadProposals = useCallback(async () => {
    if (!agentId) {
      setProposals([]);
      return;
    }
    const rows = await request<ConductorProposal[]>(
      `/api/v1/conductor/proposals?agent_id=${encodeURIComponent(agentId)}&limit=25`,
    );
    setProposals(rows ?? []);
  }, [agentId]);

  const loadContext = useCallback(async () => {
    if (!agentId) {
      setContext(null);
      return;
    }
    const qs = new URLSearchParams({ agent_id: agentId, agent_kind: agentKind });
    if (baseVersionNumber) qs.set("base_version_number", String(baseVersionNumber));
    setContext(await request<ContextResponse>(`/api/v1/conductor/context?${qs.toString()}`));
  }, [agentId, agentKind, baseVersionNumber]);

  const loadChanges = useCallback(async (proposalId: string) => {
    const rows = await request<ConductorChange[]>(
      `/api/v1/conductor/proposals/${encodeURIComponent(proposalId)}/changes`,
    );
    setChanges(rows ?? []);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    Promise.all([loadProposals(), loadContext()]).catch((err: unknown) => {
      if (cancelled) return;
      setError(
        err instanceof ApiError
          ? err.message
          : "Could not read the conductor context for this agent.",
      );
    });
    return () => {
      cancelled = true;
    };
  }, [loadProposals, loadContext]);

  useEffect(() => {
    if (!selectedId) {
      setChanges([]);
      return;
    }
    loadChanges(selectedId).catch((err: unknown) => {
      setError(err instanceof ApiError ? err.message : "Could not read the proposed changes.");
    });
  }, [selectedId, loadChanges]);

  async function act(action: () => Promise<void>) {
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      await action();
    } catch (err: unknown) {
      setError(err instanceof ApiError ? err.message : "The conductor request failed.");
    } finally {
      setBusy(false);
    }
  }

  function submitRequest() {
    void act(async () => {
      const proposal = await request<ConductorProposal>("/api/v1/conductor/proposals", {
        method: "POST",
        body: JSON.stringify({
          agent_id: agentId,
          agent_kind: agentKind,
          base_version_number: baseVersionNumber,
          request_text: requestText.trim(),
          // Validate on creation so the first thing you see is the verdict, not
          // an unvalidated plan that looks ready.
          auto_validate: true,
          auto_simulate: false,
          auto_deploy: false,
          auto_publish: false,
        }),
      });
      setRequestText("");
      setSelectedId(proposal.id);
      setNotice(
        `Proposal created: ${proposal.summary || "no summary"} · validation ${proposal.validation_status}. ` +
          "Nothing has been applied.",
      );
      await loadProposals();
    });
  }

  function post(path: string, body: Record<string, unknown> = {}) {
    void act(async () => {
      await request(`/api/v1/conductor/proposals/${encodeURIComponent(path)}`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      await loadProposals();
      if (selectedId) await loadChanges(selectedId);
    });
  }

  function applyProposal() {
    if (!selected) return;
    void act(async () => {
      const result = await request<ApplyResponse>(
        `/api/v1/conductor/proposals/${encodeURIComponent(selected.id)}/apply`,
        {
          method: "POST",
          body: JSON.stringify({
            expected_base_version_number: selected.base_version_number,
            expected_base_config_hash: selected.base_config_hash,
            version_notes: "Applied from the conductor panel.",
            auto_deploy: false,
            auto_publish: false,
          }),
        },
      );
      setNotice(
        `Applied ${result.applied_change_ids.length} change(s) into version ` +
          `${result.resulting_version_number} (from ${result.base_version_number}). ` +
          (result.production_published
            ? "Production published."
            : "Not published — a human still has to publish.") +
          (result.skipped_change_ids.length
            ? ` ${result.skipped_change_ids.length} change(s) skipped.`
            : ""),
      );
      await loadProposals();
      if (selectedId) await loadChanges(selectedId);
    });
  }

  if (!agentId) {
    return (
      <section className="card">
        <h2>Conductor</h2>
        <p className="muted">Choose an agent first; the conductor is scoped to one agent.</p>
      </section>
    );
  }

  return (
    <div data-testid="conductor-panel">
      {error ? (
        <div className="error-banner" role="alert">
          {error}
        </div>
      ) : null}
      {notice ? <div className="card">{notice}</div> : null}

      <section className="card">
        <h2>Ask the conductor</h2>
        <p className="muted">
          Describe the change. The conductor returns a proposal with per-change risk, validation and
          simulation state; nothing reaches the agent until you approve and apply.
        </p>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            submitRequest();
          }}
          style={{ display: "grid", gap: "0.75rem" }}
        >
          <label>
            Request
            <textarea
              rows={3}
              value={requestText}
              onChange={(event) => setRequestText(event.target.value)}
              placeholder="Make the agent confirm the appointment date and read it back before booking."
              data-testid="conductor-request-input"
            />
          </label>
          <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
            <button
              type="submit"
              className="btn primary"
              disabled={busy || requestText.trim() === ""}
              data-testid="conductor-submit"
            >
              Propose change
            </button>
            <button
              type="button"
              className="btn"
              onClick={() =>
                void act(async () => {
                  await loadContext();
                  setNotice("Context reloaded from the backend.");
                })
              }
              disabled={busy}
            >
              Reload context
            </button>
          </div>
        </form>
        {context ? (
          <details style={{ marginTop: "0.75rem" }}>
            <summary className="muted">
              Scoped context the conductor saw (version {String(context.version_number ?? "—")})
            </summary>
            <pre style={{ whiteSpace: "pre-wrap", fontSize: 11, maxHeight: 240, overflow: "auto" }}>
              {JSON.stringify(context, null, 2)}
            </pre>
          </details>
        ) : null}
      </section>

      <section className="card">
        <h2>Proposals</h2>
        {proposals.length === 0 ? (
          <p className="muted">No proposals for this agent yet.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Created</th>
                  <th>Summary</th>
                  <th>Base</th>
                  <th>Validation</th>
                  <th>Simulation</th>
                  <th>Status</th>
                  <th>Model</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {proposals.map((proposal) => (
                  <tr key={proposal.id}>
                    <td>{proposal.created_at ? new Date(proposal.created_at).toLocaleString() : "—"}</td>
                    <td style={{ whiteSpace: "normal", maxWidth: 320 }}>{proposal.summary}</td>
                    <td>v{proposal.base_version_number}</td>
                    <td>
                      <span className={badge(STATUS_BADGE, proposal.validation_status)}>
                        {proposal.validation_status}
                      </span>
                    </td>
                    <td>
                      <span className={badge(STATUS_BADGE, proposal.simulation_status)}>
                        {proposal.simulation_status}
                      </span>
                    </td>
                    <td>
                      <span className={badge(STATUS_BADGE, proposal.status)}>{proposal.status}</span>
                    </td>
                    <td>
                      <code>
                        {proposal.provider}/{proposal.model}
                      </code>
                      {proposal.is_mock_provider ? (
                        <span className="badge paused" style={{ marginLeft: 6 }}>
                          mock provider
                        </span>
                      ) : null}
                    </td>
                    <td>
                      <button
                        type="button"
                        className="btn"
                        onClick={() => setSelectedId(proposal.id)}
                      >
                        Inspect
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {selected ? (
        <section className="card" data-testid="conductor-proposal-detail">
          <h2>Proposal {selected.id.slice(0, 8)}</h2>
          <p className="muted">{selected.rationale}</p>
          <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", marginBottom: "0.75rem" }}>
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() => post(`${selected.id}/validate`)}
            >
              Re-validate
            </button>
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() => post(`${selected.id}/simulate`, {})}
            >
              Simulate
            </button>
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() => post(`${selected.id}/approve`, { reason: "Reviewed in the panel." })}
            >
              Approve
            </button>
            <button
              type="button"
              className="btn primary"
              disabled={busy || selected.status === "applied"}
              onClick={applyProposal}
              data-testid="conductor-apply"
            >
              Apply to new version
            </button>
          </div>

          {changes.length === 0 ? (
            <p className="muted">This proposal carries no changes.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Section</th>
                    <th>Path</th>
                    <th>Op</th>
                    <th>Risk</th>
                    <th>Validation</th>
                    <th>Approval</th>
                    <th>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {changes.map((change) => (
                    <tr key={change.id}>
                      <td>{change.sequence}</td>
                      <td>{change.section}</td>
                      <td>
                        <code>{change.path}</code>
                      </td>
                      <td>{change.operation}</td>
                      <td>
                        <span className={badge(RISK_BADGE, change.risk_level)}>
                          {change.risk_level}
                        </span>
                      </td>
                      <td>
                        <span className={badge(STATUS_BADGE, change.validation_state)}>
                          {change.validation_state}
                        </span>
                        {change.validation_messages.length ? (
                          <div className="muted" style={{ fontSize: 11 }}>
                            {change.validation_messages.join("; ")}
                          </div>
                        ) : null}
                      </td>
                      <td>{change.approval_state}</td>
                      <td style={{ whiteSpace: "normal", maxWidth: 260 }}>{change.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      ) : null}
    </div>
  );
}
