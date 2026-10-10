// File: dashboard-next/app/dashboard/agents/[id]/page.tsx — Agent detail workspace with Config, Flow, Versions (diff/publish/rollback), Tools, Knowledge, and Test tabs (Part 5 / Gate G6)

"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import { GlassBadge } from "@/components/enterprise/GlassBadge";
import { GlassCard } from "@/components/enterprise/GlassCard";
import { SectionHeader } from "@/components/enterprise/SectionHeader";

type AgentDetailTab = "Config" | "Flow" | "Versions" | "Tools" | "Knowledge" | "Test";

const TABS: AgentDetailTab[] = ["Config", "Flow", "Versions", "Tools", "Knowledge", "Test"];

export default function AgentDetailPage() {
  const params = useParams<{ id: string }>();
  const agentId = String(params?.id ?? "default");

  const [activeTab, setActiveTab] = useState<AgentDetailTab>("Config");
  const [agent, setAgent] = useState<Record<string, unknown> | null>(null);
  const [draftConfig, setDraftConfig] = useState<Record<string, unknown>>({});
  const [draftEtag, setDraftEtag] = useState<string>("");
  const [flowInfo, setFlowInfo] = useState<Record<string, unknown> | null>(null);
  const [versions, setVersions] = useState<Array<Record<string, unknown>>>([]);
  const [diffResult, setDiffResult] = useState<Record<string, unknown> | null>(null);
  const [diffFrom, setDiffFrom] = useState<number>(1);
  const [diffTo, setDiffTo] = useState<number>(1);
  const [changelog, setChangelog] = useState<string>("Published from Agent Workspace");

  // W-12 entities: Tools, WorkflowTriggers, KnowledgeCollections
  const [tools, setTools] = useState<Array<Record<string, unknown>>>([]);
  const [triggers, setTriggers] = useState<Array<Record<string, unknown>>>([]);
  const [collections, setCollections] = useState<Array<Record<string, unknown>>>([]);
  const [newToolName, setNewToolName] = useState<string>("");
  const [newToolUrl, setNewToolUrl] = useState<string>("https://api.example.com/crm/lookup");
  const [newTriggerEvent, setNewTriggerEvent] = useState<string>("call_ended");
  const [newCollectionName, setNewCollectionName] = useState<string>("");

  // Test simulator state
  const [simTurnsText, setSimTurnsText] = useState<string>(
    "I need help from sales\nPlease look up my CRM account and transfer me",
  );
  const [simResult, setSimResult] = useState<Record<string, unknown> | null>(null);

  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  const loadWorkspace = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [agentRow, flowRes, versionRows, toolsRes, collectionsRes, triggersRes] =
        await Promise.all([
          api.getAgent(agentId),
          api.getAgentFlow(agentId),
          api.listAgentVersions(agentId),
          api.listAgentTools(agentId).catch(() => ({ items: [] })),
          api.listKnowledgeCollections().catch(() => ({ items: [] })),
          api.listWorkflowTriggers(agentId).catch(() => ({ items: [] })),
        ]);

      setAgent(agentRow);
      const currentDraft =
        agentRow.current_draft_config && typeof agentRow.current_draft_config === "object"
          ? (agentRow.current_draft_config as Record<string, unknown>)
          : {};
      setDraftConfig(currentDraft);
      setDraftEtag(String(agentRow.draft_etag ?? flowRes.draft_etag ?? ""));
      setFlowInfo(flowRes);
      const vList = Array.isArray(versionRows) ? versionRows : [];
      setVersions(vList);
      if (vList.length >= 2) {
        setDiffFrom(Number(vList[vList.length - 1]?.version_number ?? 1));
        setDiffTo(Number(vList[0]?.version_number ?? 1));
      }

      const toolItems = Array.isArray(toolsRes.items) ? toolsRes.items : [];
      setTools(toolItems as Array<Record<string, unknown>>);
      const colItems = Array.isArray(collectionsRes.items) ? collectionsRes.items : [];
      setCollections(colItems as Array<Record<string, unknown>>);
      const trigItems = Array.isArray(triggersRes.items) ? triggersRes.items : [];
      setTriggers(trigItems as Array<Record<string, unknown>>);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load agent detail");
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    void loadWorkspace();
  }, [loadWorkspace]);

  const handleSaveConfig = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setStatusMessage(null);
    try {
      const res = await api.putAgentDraft(agentId, draftConfig, draftEtag || undefined);
      setDraftEtag(String(res.draft_etag ?? ""));
      setStatusMessage("Saved agent configuration draft.");
      await loadWorkspace();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save draft config");
    }
  };

  const handlePublish = async () => {
    setError(null);
    setStatusMessage(null);
    try {
      const published = await api.publishAgentVersion(agentId, {
        changelog,
        environment: "production",
      });
      setStatusMessage(`Published immutable version v${String(published.version ?? published.version_number ?? "")}.`);
      await loadWorkspace();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Publish failed");
    }
  };

  const handleDiffVersions = async () => {
    setError(null);
    try {
      const diff = await api.diffAgentVersions(agentId, diffFrom, diffTo);
      setDiffResult(diff);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to diff versions");
    }
  };

  const handleRollback = async (targetVersion: number) => {
    setError(null);
    setStatusMessage(null);
    try {
      await api.rollbackAgentVersion(agentId, {
        target_version: targetVersion,
        reason: `Rolled back to v${targetVersion} from Agent Detail`,
      });
      setStatusMessage(`Rolled back active configuration to v${targetVersion}.`);
      await loadWorkspace();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Rollback failed");
    }
  };

  const handleCreateTool = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newToolName.trim()) return;
    setError(null);
    try {
      await api.createAgentTool({
        name: newToolName.trim(),
        tool_type: "api",
        agent_id: agentId,
        endpoint_url: newToolUrl.trim(),
        http_method: "POST",
        parameters_schema: { type: "object", properties: { query: { type: "string" } } },
      });
      setNewToolName("");
      setStatusMessage("Registered AgentTool.");
      await loadWorkspace();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to register tool");
    }
  };

  const handleCreateTrigger = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await api.createWorkflowTrigger({
        event_type: newTriggerEvent,
        agent_id: agentId,
        action_type: "webhook",
        action_config: { url: "https://hooks.example.com/voxdesk" },
      });
      setStatusMessage(`Created WorkflowTrigger for ${newTriggerEvent}.`);
      await loadWorkspace();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create workflow trigger");
    }
  };

  const handleCreateCollection = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newCollectionName.trim()) return;
    setError(null);
    try {
      await api.createKnowledgeCollection({
        name: newCollectionName.trim(),
        description: "Bound to conversation flow RAG context",
        chunk_size: 512,
        chunk_overlap: 64,
      });
      setNewCollectionName("");
      setStatusMessage("Created KnowledgeCollection.");
      await loadWorkspace();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create knowledge collection");
    }
  };

  const handleRunSimulation = async () => {
    setError(null);
    try {
      const turns = simTurnsText
        .split("\n")
        .map((l) => l.trim())
        .filter(Boolean);
      const res = await api.simulateAgentFlow(agentId, { turns });
      setSimResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Flow simulation failed");
    }
  };

  const inputStyle: React.CSSProperties = {
    width: "100%",
    padding: "0.5rem 0.7rem",
    borderRadius: "0.45rem",
    border: "1px solid rgba(148, 163, 184, 0.25)",
    background: "rgba(15, 23, 42, 0.85)",
    color: "#f8fafc",
    fontSize: "0.82rem",
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem", padding: "1.5rem" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <SectionHeader
          eyebrow={`Agent Workspace · ${agentId}`}
          title={String(agent?.name ?? "Agent Detail")}
          description={String(agent?.description ?? "Configure prompt, visual flow, versions, tools, knowledge, and test simulations.")}
        />
        <div style={{ display: "flex", gap: "0.6rem" }}>
          <Link
            href="/dashboard/agents"
            style={{
              padding: "0.45rem 0.85rem",
              borderRadius: "0.5rem",
              border: "1px solid rgba(148, 163, 184, 0.25)",
              background: "rgba(30, 41, 59, 0.75)",
              color: "#cbd5e1",
              fontSize: "0.78rem",
              textDecoration: "none",
            }}
          >
            ← All Agents
          </Link>
          <Link
            href={`/dashboard/agents/${encodeURIComponent(agentId)}/flow`}
            data-testid="open-flow-canvas-btn"
            style={{
              padding: "0.45rem 0.95rem",
              borderRadius: "0.5rem",
              border: "1px solid rgba(16, 185, 129, 0.45)",
              background: "rgba(16, 185, 129, 0.2)",
              color: "#6ee7b7",
              fontSize: "0.78rem",
              fontWeight: 700,
              textDecoration: "none",
            }}
          >
            Open Visual Flow Builder →
          </Link>
        </div>
      </div>

      {/* Tab Bar */}
      <div
        role="tablist"
        aria-label="Agent Detail Tabs"
        style={{
          display: "flex",
          gap: "0.4rem",
          borderBottom: "1px solid rgba(148, 163, 184, 0.2)",
          paddingBottom: "0.5rem",
        }}
      >
        {TABS.map((tab) => (
          <button
            key={tab}
            type="button"
            role="tab"
            aria-selected={activeTab === tab}
            data-testid={`agent-tab-${tab.toLowerCase()}`}
            onClick={() => setActiveTab(tab)}
            style={{
              padding: "0.45rem 0.9rem",
              borderRadius: "0.45rem",
              border:
                activeTab === tab
                  ? "1px solid rgba(99, 102, 241, 0.55)"
                  : "1px solid transparent",
              background:
                activeTab === tab
                  ? "rgba(99, 102, 241, 0.22)"
                  : "rgba(15, 23, 42, 0.5)",
              color: activeTab === tab ? "#e0e7ff" : "#94a3b8",
              fontWeight: activeTab === tab ? 700 : 500,
              fontSize: "0.82rem",
              cursor: "pointer",
            }}
          >
            {tab}
          </button>
        ))}
      </div>

      {error && (
        <div
          role="alert"
          style={{
            padding: "0.75rem 1rem",
            borderRadius: "0.6rem",
            background: "rgba(239, 68, 68, 0.16)",
            border: "1px solid rgba(239, 68, 68, 0.4)",
            color: "#fca5a5",
            fontSize: "0.82rem",
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
            fontSize: "0.82rem",
          }}
        >
          {statusMessage}
        </div>
      )}

      {loading ? (
        <GlassCard>
          <div style={{ color: "#94a3b8" }}>Loading agent workspace...</div>
        </GlassCard>
      ) : (
        <>
          {activeTab === "Config" && (
            <GlassCard>
              <form
                onSubmit={handleSaveConfig}
                data-testid="agent-config-form"
                style={{ display: "flex", flexDirection: "column", gap: "0.9rem" }}
              >
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.9rem" }}>
                  <label style={{ display: "flex", flexDirection: "column", gap: "0.3rem", fontSize: "0.78rem", color: "#cbd5e1" }}>
                    Execution Mode
                    <select
                      value={String(draftConfig.mode ?? "single_prompt")}
                      onChange={(e) =>
                        setDraftConfig({ ...draftConfig, mode: e.target.value })
                      }
                      style={inputStyle}
                    >
                      <option value="single_prompt">Single Prompt Mode</option>
                      <option value="flow">Conversation Flow Mode</option>
                    </select>
                  </label>
                  <label style={{ display: "flex", flexDirection: "column", gap: "0.3rem", fontSize: "0.78rem", color: "#cbd5e1" }}>
                    Opening Greeting
                    <input
                      type="text"
                      value={String(draftConfig.greeting ?? "")}
                      onChange={(e) =>
                        setDraftConfig({ ...draftConfig, greeting: e.target.value })
                      }
                      style={inputStyle}
                    />
                  </label>
                </div>

                <label style={{ display: "flex", flexDirection: "column", gap: "0.3rem", fontSize: "0.78rem", color: "#cbd5e1" }}>
                  Base System Prompt
                  <textarea
                    rows={5}
                    value={String(draftConfig.system_prompt ?? "")}
                    onChange={(e) =>
                      setDraftConfig({ ...draftConfig, system_prompt: e.target.value })
                    }
                    style={inputStyle}
                  />
                </label>

                <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.6rem" }}>
                  <button
                    type="submit"
                    style={{
                      padding: "0.5rem 1rem",
                      borderRadius: "0.45rem",
                      border: "1px solid rgba(99, 102, 241, 0.5)",
                      background: "#4f46e5",
                      color: "#ffffff",
                      fontWeight: 600,
                      cursor: "pointer",
                    }}
                  >
                    Save Draft Config
                  </button>
                </div>
              </form>
            </GlassCard>
          )}

          {activeTab === "Flow" && (
            <GlassCard>
              <div style={{ display: "flex", flexDirection: "column", gap: "0.85rem" }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <div>
                    <div style={{ fontSize: "1rem", fontWeight: 700, color: "#f8fafc" }}>
                      Conversation Flow Graph
                    </div>
                    <div style={{ fontSize: "0.78rem", color: "#94a3b8" }}>
                      Mode: {String(flowInfo?.mode ?? "flow")} · Draft ETag: {draftEtag || "none"}
                    </div>
                  </div>
                  <Link
                    href={`/dashboard/agents/${encodeURIComponent(agentId)}/flow`}
                    style={{
                      padding: "0.45rem 0.9rem",
                      borderRadius: "0.45rem",
                      background: "#4f46e5",
                      color: "#ffffff",
                      fontSize: "0.8rem",
                      fontWeight: 600,
                      textDecoration: "none",
                    }}
                  >
                    Launch React Flow Canvas →
                  </Link>
                </div>
              </div>
            </GlassCard>
          )}

          {activeTab === "Versions" && (
            <GlassCard>
              <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                <div style={{ display: "flex", gap: "0.6rem", alignItems: "end" }}>
                  <label style={{ flex: 1, display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.76rem", color: "#cbd5e1" }}>
                    Release Changelog
                    <input
                      type="text"
                      value={changelog}
                      onChange={(e) => setChangelog(e.target.value)}
                      style={inputStyle}
                    />
                  </label>
                  <button
                    type="button"
                    data-testid="publish-version-btn"
                    onClick={() => void handlePublish()}
                    style={{
                      padding: "0.52rem 1rem",
                      borderRadius: "0.45rem",
                      border: "1px solid rgba(16, 185, 129, 0.5)",
                      background: "rgba(16, 185, 129, 0.22)",
                      color: "#6ee7b7",
                      fontWeight: 700,
                      cursor: "pointer",
                    }}
                  >
                    Publish New Version
                  </button>
                </div>

                <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                  <span style={{ fontSize: "0.78rem", color: "#94a3b8" }}>Compare v</span>
                  <input
                    type="number"
                    min={1}
                    value={diffFrom}
                    onChange={(e) => setDiffFrom(Number(e.target.value) || 1)}
                    style={{ ...inputStyle, width: "80px" }}
                  />
                  <span style={{ fontSize: "0.78rem", color: "#94a3b8" }}>to v</span>
                  <input
                    type="number"
                    min={1}
                    value={diffTo}
                    onChange={(e) => setDiffTo(Number(e.target.value) || 1)}
                    style={{ ...inputStyle, width: "80px" }}
                  />
                  <button
                    type="button"
                    data-testid="diff-versions-btn"
                    onClick={() => void handleDiffVersions()}
                    style={{
                      padding: "0.42rem 0.8rem",
                      borderRadius: "0.45rem",
                      border: "1px solid rgba(99, 102, 241, 0.45)",
                      background: "rgba(99, 102, 241, 0.2)",
                      color: "#c7d2fe",
                      fontSize: "0.76rem",
                      cursor: "pointer",
                    }}
                  >
                    Diff Versions
                  </button>
                </div>

                {diffResult && (
                  <pre
                    data-testid="version-diff-output"
                    style={{
                      padding: "0.75rem",
                      borderRadius: "0.5rem",
                      background: "rgba(2, 6, 23, 0.75)",
                      color: "#cbd5e1",
                      fontSize: "0.74rem",
                      overflowX: "auto",
                    }}
                  >
                    {JSON.stringify(diffResult, null, 2)}
                  </pre>
                )}

                <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
                  {versions.map((ver) => {
                    const vNum = Number(ver.version_number ?? ver.version ?? 1);
                    return (
                      <div
                        key={String(ver.id ?? vNum)}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "space-between",
                          padding: "0.65rem 0.85rem",
                          borderRadius: "0.5rem",
                          background: "rgba(15, 23, 42, 0.75)",
                          border: "1px solid rgba(148, 163, 184, 0.2)",
                        }}
                      >
                        <div>
                          <span style={{ fontWeight: 700, color: "#f8fafc" }}>v{vNum}</span>
                          <span style={{ marginLeft: "0.6rem", fontSize: "0.78rem", color: "#94a3b8" }}>
                            {String(ver.changelog ?? "Published snapshot")}
                          </span>
                        </div>
                        <div style={{ display: "flex", gap: "0.45rem", alignItems: "center" }}>
                          <GlassBadge variant="neutral">
                            {String(ver.status ?? "published")}
                          </GlassBadge>
                          <button
                            type="button"
                            data-testid={`rollback-version-${vNum}`}
                            onClick={() => void handleRollback(vNum)}
                            style={{
                              padding: "0.28rem 0.65rem",
                              borderRadius: "0.4rem",
                              border: "1px solid rgba(245, 158, 11, 0.45)",
                              background: "rgba(245, 158, 11, 0.16)",
                              color: "#fcd34d",
                              fontSize: "0.74rem",
                              cursor: "pointer",
                            }}
                          >
                            Rollback to v{vNum}
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </GlassCard>
          )}

          {activeTab === "Tools" && (
            <GlassCard>
              <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                <form
                  onSubmit={handleCreateTool}
                  style={{ display: "grid", gridTemplateColumns: "1fr 1.5fr auto", gap: "0.6rem", alignItems: "end" }}
                >
                  <label style={{ fontSize: "0.76rem", color: "#cbd5e1" }}>
                    AgentTool Name
                    <input
                      type="text"
                      placeholder="lookup_customer"
                      value={newToolName}
                      onChange={(e) => setNewToolName(e.target.value)}
                      style={inputStyle}
                    />
                  </label>
                  <label style={{ fontSize: "0.76rem", color: "#cbd5e1" }}>
                    HTTPS Endpoint URL
                    <input
                      type="url"
                      value={newToolUrl}
                      onChange={(e) => setNewToolUrl(e.target.value)}
                      style={inputStyle}
                    />
                  </label>
                  <button
                    type="submit"
                    style={{
                      padding: "0.5rem 0.9rem",
                      borderRadius: "0.45rem",
                      background: "#4f46e5",
                      color: "#fff",
                      border: "none",
                      cursor: "pointer",
                    }}
                  >
                    + Register AgentTool
                  </button>
                </form>

                <form
                  onSubmit={handleCreateTrigger}
                  style={{ display: "flex", gap: "0.6rem", alignItems: "end" }}
                >
                  <label style={{ fontSize: "0.76rem", color: "#cbd5e1" }}>
                    WorkflowTrigger Event
                    <select
                      value={newTriggerEvent}
                      onChange={(e) => setNewTriggerEvent(e.target.value)}
                      style={inputStyle}
                    >
                      <option value="call_started">call_started</option>
                      <option value="call_ended">call_ended</option>
                      <option value="appointment_booked">appointment_booked</option>
                      <option value="escalation_triggered">escalation_triggered</option>
                    </select>
                  </label>
                  <button
                    type="submit"
                    style={{
                      padding: "0.5rem 0.9rem",
                      borderRadius: "0.45rem",
                      background: "rgba(20, 184, 166, 0.25)",
                      color: "#5eead4",
                      border: "1px solid rgba(20, 184, 166, 0.45)",
                      cursor: "pointer",
                    }}
                  >
                    + Add WorkflowTrigger
                  </button>
                </form>

                <div style={{ fontSize: "0.8rem", color: "#94a3b8" }}>
                  Registered AgentTools ({tools.length}) · WorkflowTriggers ({triggers.length})
                </div>
              </div>
            </GlassCard>
          )}

          {activeTab === "Knowledge" && (
            <GlassCard>
              <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                <form
                  onSubmit={handleCreateCollection}
                  style={{ display: "flex", gap: "0.6rem", alignItems: "end" }}
                >
                  <label style={{ flex: 1, fontSize: "0.76rem", color: "#cbd5e1" }}>
                    KnowledgeCollection Name
                    <input
                      type="text"
                      placeholder="Enterprise Pricing & SLA FAQ"
                      value={newCollectionName}
                      onChange={(e) => setNewCollectionName(e.target.value)}
                      style={inputStyle}
                    />
                  </label>
                  <button
                    type="submit"
                    style={{
                      padding: "0.5rem 0.9rem",
                      borderRadius: "0.45rem",
                      background: "#4f46e5",
                      color: "#fff",
                      border: "none",
                      cursor: "pointer",
                    }}
                  >
                    + Create Collection
                  </button>
                </form>
                <div style={{ fontSize: "0.8rem", color: "#94a3b8" }}>
                  Active KnowledgeCollections ({collections.length})
                </div>
              </div>
            </GlassCard>
          )}

          {activeTab === "Test" && (
            <GlassCard>
              <div style={{ display: "flex", flexDirection: "column", gap: "0.85rem" }}>
                <label style={{ display: "flex", flexDirection: "column", gap: "0.3rem", fontSize: "0.78rem", color: "#cbd5e1" }}>
                  Scripted Caller Utterances (one turn per line)
                  <textarea
                    rows={4}
                    data-testid="simulate-turns-input"
                    value={simTurnsText}
                    onChange={(e) => setSimTurnsText(e.target.value)}
                    style={inputStyle}
                  />
                </label>
                <div>
                  <button
                    type="button"
                    data-testid="run-flow-simulation-btn"
                    onClick={() => void handleRunSimulation()}
                    style={{
                      padding: "0.5rem 1rem",
                      borderRadius: "0.45rem",
                      background: "#10b981",
                      color: "#022c22",
                      fontWeight: 700,
                      border: "none",
                      cursor: "pointer",
                    }}
                  >
                    Run FlowRunner Simulation
                  </button>
                </div>
                {simResult && (
                  <pre
                    data-testid="flow-simulation-output"
                    style={{
                      padding: "0.75rem",
                      borderRadius: "0.5rem",
                      background: "rgba(2, 6, 23, 0.8)",
                      color: "#a7f3d0",
                      fontSize: "0.74rem",
                      overflowX: "auto",
                    }}
                  >
                    {JSON.stringify(simResult, null, 2)}
                  </pre>
                )}
              </div>
            </GlassCard>
          )}
        </>
      )}
    </div>
  );
}
