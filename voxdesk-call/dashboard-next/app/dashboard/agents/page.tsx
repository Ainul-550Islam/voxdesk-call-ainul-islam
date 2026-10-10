// File: dashboard-next/app/dashboard/agents/page.tsx — Multi-agent workspace list with create, duplicate, archive, and flow builder links (Part 5 / Gate G6)

"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { GlassBadge } from "@/components/enterprise/GlassBadge";
import { GlassCard } from "@/components/enterprise/GlassCard";
import { SectionHeader } from "@/components/enterprise/SectionHeader";

interface AgentSummaryItem {
  id: string;
  external_key?: string;
  name: string;
  description?: string;
  status: string;
  published_version_number?: number | null;
  draft_etag?: string | null;
  validation_status?: string;
  updated_at?: string;
}

export default function AgentsListPage() {
  const [agents, setAgents] = useState<AgentSummaryItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState<string>("");
  const [newDescription, setNewDescription] = useState<string>("");
  const [newMode, setNewMode] = useState<"single_prompt" | "flow">("flow");
  const [creating, setCreating] = useState<boolean>(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  const loadAgents = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const rows = await api.listAgents();
      const normalized: AgentSummaryItem[] = (Array.isArray(rows) ? rows : []).map((r) => ({
        id: String(r.id ?? ""),
        external_key: r.external_key ? String(r.external_key) : undefined,
        name: String(r.name ?? "Untitled Agent"),
        description: r.description ? String(r.description) : "",
        status: String(r.status ?? "draft"),
        published_version_number:
          typeof r.published_version_number === "number"
            ? r.published_version_number
            : null,
        draft_etag: r.draft_etag ? String(r.draft_etag) : null,
        validation_status: r.validation_status ? String(r.validation_status) : "valid",
        updated_at: r.updated_at ? String(r.updated_at) : undefined,
      }));
      setAgents(normalized);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load agents");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadAgents();
  }, [loadAgents]);

  const handleCreateAgent = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newName.trim()) return;
    setCreating(true);
    setError(null);
    setStatusMessage(null);
    try {
      const created = await api.createAgent({
        name: newName.trim(),
        description: newDescription.trim(),
        initial_config: {
          mode: newMode,
        },
      });
      setNewName("");
      setNewDescription("");
      setStatusMessage(`Created agent "${String(created.name ?? newName.trim())}".`);
      await loadAgents();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create agent");
    } finally {
      setCreating(false);
    }
  };

  const handleDuplicate = async (agentId: string, name: string) => {
    setError(null);
    setStatusMessage(null);
    try {
      await api.duplicateAgent(agentId, { name: `${name} (Copy)`, include_draft: true });
      setStatusMessage(`Duplicated "${name}".`);
      await loadAgents();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to duplicate agent");
    }
  };

  const handleArchive = async (agentId: string, name: string) => {
    setError(null);
    setStatusMessage(null);
    try {
      await api.archiveAgent(agentId, "Archived from multi-agent workspace");
      setStatusMessage(`Archived "${name}".`);
      await loadAgents();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to archive agent");
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem", padding: "1.5rem" }}>
      <SectionHeader
        eyebrow="Voice AI Control Plane · Gate G6"
        title="Multi-Agent Directory & Flow Studio"
        description="Create, duplicate, version, and build visual conversation-flow graphs for voice and web agents."
      />

      {error && (
        <div
          role="alert"
          style={{
            padding: "0.75rem 1rem",
            borderRadius: "0.6rem",
            background: "rgba(239, 68, 68, 0.16)",
            border: "1px solid rgba(239, 68, 68, 0.4)",
            color: "#fca5a5",
            fontSize: "0.84rem",
          }}
        >
          {error}
        </div>
      )}

      {statusMessage && (
        <div
          role="status"
          style={{
            padding: "0.75rem 1rem",
            borderRadius: "0.6rem",
            background: "rgba(16, 185, 129, 0.16)",
            border: "1px solid rgba(16, 185, 129, 0.4)",
            color: "#6ee7b7",
            fontSize: "0.84rem",
          }}
        >
          {statusMessage}
        </div>
      )}

      <GlassCard>
        <form
          onSubmit={handleCreateAgent}
          data-testid="create-agent-form"
          style={{
            display: "grid",
            gridTemplateColumns: "1.2fr 1.6fr 170px auto",
            gap: "0.75rem",
            alignItems: "end",
          }}
        >
          <label style={{ display: "flex", flexDirection: "column", gap: "0.3rem", fontSize: "0.76rem", color: "#cbd5e1" }}>
            Agent Name
            <input
              type="text"
              required
              placeholder="e.g. Inbound Sales Qualifier"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              style={{
                padding: "0.5rem 0.7rem",
                borderRadius: "0.45rem",
                border: "1px solid rgba(148, 163, 184, 0.25)",
                background: "rgba(15, 23, 42, 0.85)",
                color: "#f8fafc",
              }}
            />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: "0.3rem", fontSize: "0.76rem", color: "#cbd5e1" }}>
            Description
            <input
              type="text"
              placeholder="Qualifies inbound leads, calls CRM tool, warm transfers to AE"
              value={newDescription}
              onChange={(e) => setNewDescription(e.target.value)}
              style={{
                padding: "0.5rem 0.7rem",
                borderRadius: "0.45rem",
                border: "1px solid rgba(148, 163, 184, 0.25)",
                background: "rgba(15, 23, 42, 0.85)",
                color: "#f8fafc",
              }}
            />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: "0.3rem", fontSize: "0.76rem", color: "#cbd5e1" }}>
            Engine Mode
            <select
              value={newMode}
              onChange={(e) => setNewMode(e.target.value as "single_prompt" | "flow")}
              style={{
                padding: "0.5rem 0.7rem",
                borderRadius: "0.45rem",
                border: "1px solid rgba(148, 163, 184, 0.25)",
                background: "rgba(15, 23, 42, 0.85)",
                color: "#f8fafc",
              }}
            >
              <option value="flow">Conversation Flow</option>
              <option value="single_prompt">Single Prompt</option>
            </select>
          </label>
          <button
            type="submit"
            disabled={creating}
            data-testid="create-agent-submit"
            style={{
              padding: "0.55rem 1.1rem",
              borderRadius: "0.5rem",
              border: "1px solid rgba(99, 102, 241, 0.5)",
              background: "linear-gradient(135deg, #6366f1, #4f46e5)",
              color: "#ffffff",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            {creating ? "Creating..." : "+ New Agent"}
          </button>
        </form>
      </GlassCard>

      {loading ? (
        <GlassCard>
          <div style={{ color: "#94a3b8", fontSize: "0.86rem" }}>Loading agents...</div>
        </GlassCard>
      ) : agents.length === 0 ? (
        <GlassCard>
          <div style={{ color: "#94a3b8", fontSize: "0.86rem" }}>
            No agents found for this tenant. Create your first agent above.
          </div>
        </GlassCard>
      ) : (
        <div
          data-testid="agents-grid"
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))",
            gap: "1rem",
          }}
        >
          {agents.map((agent) => (
            <GlassCard key={agent.id}>
              <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <div>
                    <div style={{ fontSize: "1rem", fontWeight: 700, color: "#f8fafc" }}>
                      {agent.name}
                    </div>
                    <div style={{ fontSize: "0.72rem", color: "#64748b" }}>
                      ID: {agent.external_key || agent.id}
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: "0.35rem" }}>
                    <GlassBadge
                      variant={
                        agent.status === "active"
                          ? "success"
                          : agent.status === "archived"
                          ? "danger"
                          : "warning"
                      }
                    >
                      {agent.status.toUpperCase()}
                    </GlassBadge>
                    {agent.published_version_number && (
                      <GlassBadge variant="accent">
                        v{agent.published_version_number}
                      </GlassBadge>
                    )}
                  </div>
                </div>

                <div style={{ fontSize: "0.8rem", color: "#94a3b8", minHeight: "2.2rem" }}>
                  {agent.description || "No description provided."}
                </div>

                <div style={{ display: "flex", flexWrap: "wrap", gap: "0.45rem", marginTop: "0.25rem" }}>
                  <Link
                    href={`/dashboard/agents/${encodeURIComponent(agent.id)}`}
                    data-testid={`agent-detail-link-${agent.id}`}
                    style={{
                      padding: "0.38rem 0.7rem",
                      borderRadius: "0.45rem",
                      background: "rgba(99, 102, 241, 0.2)",
                      border: "1px solid rgba(99, 102, 241, 0.4)",
                      color: "#e0e7ff",
                      fontSize: "0.75rem",
                      fontWeight: 600,
                      textDecoration: "none",
                    }}
                  >
                    Open Workspace
                  </Link>
                  <Link
                    href={`/dashboard/agents/${encodeURIComponent(agent.id)}/flow`}
                    data-testid={`agent-flow-link-${agent.id}`}
                    style={{
                      padding: "0.38rem 0.7rem",
                      borderRadius: "0.45rem",
                      background: "rgba(16, 185, 129, 0.18)",
                      border: "1px solid rgba(16, 185, 129, 0.4)",
                      color: "#6ee7b7",
                      fontSize: "0.75rem",
                      fontWeight: 600,
                      textDecoration: "none",
                    }}
                  >
                    Flow Canvas
                  </Link>
                  <button
                    type="button"
                    data-testid={`duplicate-agent-${agent.id}`}
                    onClick={() => void handleDuplicate(agent.id, agent.name)}
                    style={{
                      padding: "0.38rem 0.7rem",
                      borderRadius: "0.45rem",
                      background: "rgba(30, 41, 59, 0.75)",
                      border: "1px solid rgba(148, 163, 184, 0.25)",
                      color: "#cbd5e1",
                      fontSize: "0.75rem",
                      cursor: "pointer",
                    }}
                  >
                    Duplicate
                  </button>
                  {agent.status !== "archived" && (
                    <button
                      type="button"
                      data-testid={`archive-agent-${agent.id}`}
                      onClick={() => void handleArchive(agent.id, agent.name)}
                      style={{
                        padding: "0.38rem 0.7rem",
                        borderRadius: "0.45rem",
                        background: "rgba(239, 68, 68, 0.14)",
                        border: "1px solid rgba(239, 68, 68, 0.35)",
                        color: "#fca5a5",
                        fontSize: "0.75rem",
                        cursor: "pointer",
                      }}
                    >
                      Archive
                    </button>
                  )}
                </div>
              </div>
            </GlassCard>
          ))}
        </div>
      )}
    </div>
  );
}
