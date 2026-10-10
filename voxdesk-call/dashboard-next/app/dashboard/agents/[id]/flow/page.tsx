// File: dashboard-next/app/dashboard/agents/[id]/flow/page.tsx — React Flow conversation-flow builder canvas with palette, drag/drop, edge condition editor, inspector, mini-map, validation panel, undo/redo, autosave draft, and publish (Part 5 / Gate G6)

"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  Background,
  Connection,
  Controls,
  Edge as RFEdge,
  MiniMap,
  Node as RFNode,
  ReactFlow,
} from "@xyflow/react";
import { api } from "@/lib/api";
import {
  FlowEdge,
  FlowGraph,
  FlowGraphSchema,
  FlowNode,
  FlowNodeType,
  SAMPLE_APPOINTMENT_FLOW_FIXTURE,
  autoLayoutFlowGraph,
  validateFlowGraphClient,
} from "@/lib/flow-schema";
import { NODE_PALETTE_ITEMS, Palette } from "@/components/flow/Palette";
import { FlowNodeCardView, NodeCard } from "@/components/flow/NodeCard";
import { Inspector } from "@/components/flow/Inspector";
import { ValidationPanel } from "@/components/flow/ValidationPanel";

const NODE_TYPES = {
  flowNode: NodeCard,
};

export interface FlowEditorWorkspaceProps {
  agentId?: string;
  initialGraph?: FlowGraph;
  disableInitialFetch?: boolean;
}

function FlowEditorWorkspace({
  agentId = "default",
  initialGraph,
  disableInitialFetch = false,
}: FlowEditorWorkspaceProps) {
  const [graph, setGraph] = useState<FlowGraph>(
    () => initialGraph ?? SAMPLE_APPOINTMENT_FLOW_FIXTURE,
  );
  const [past, setPast] = useState<FlowGraph[]>([]);
  const [future, setFuture] = useState<FlowGraph[]>([]);

  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(
    () => (initialGraph ?? SAMPLE_APPOINTMENT_FLOW_FIXTURE).nodes[0]?.id ?? null,
  );
  const [connectSourceId, setConnectSourceId] = useState<string>("");
  const [connectTargetId, setConnectTargetId] = useState<string>("");

  const [draftEtag, setDraftEtag] = useState<string>("");
  const [availableTools, setAvailableTools] = useState<string[]>([
    "lookup_customer",
    "book_appointment",
    "transfer_to_human",
    "send_sms_confirmation",
  ]);
  const [availableKnowledgeIds, setAvailableKnowledgeIds] = useState<string[]>([]);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [statusBanner, setStatusBanner] = useState<string | null>(null);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);
  const autosaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Load initial flow from backend unless disabled by unit test
  useEffect(() => {
    if (disableInitialFetch || initialGraph) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await api.getAgentFlow(agentId);
        if (cancelled) return;
        if (res.flow && typeof res.flow === "object") {
          const parsed = FlowGraphSchema.safeParse(res.flow);
          if (parsed.success) {
            setGraph(parsed.data);
            setSelectedNodeId(parsed.data.nodes[0]?.id ?? null);
          }
        }
        if (typeof res.draft_etag === "string") {
          setDraftEtag(res.draft_etag);
        }
        const bindings = res.bindings as Record<string, unknown> | undefined;
        if (bindings && Array.isArray(bindings.tools)) {
          const names = (bindings.tools as Array<Record<string, unknown>>)
            .map((t) => {
              const fn = t.function as Record<string, unknown> | undefined;
              return String(fn?.name ?? t.name ?? "");
            })
            .filter(Boolean);
          if (names.length > 0) {
            setAvailableTools((prev) => Array.from(new Set([...prev, ...names])));
          }
        }
        if (bindings && Array.isArray(bindings.knowledge_collections)) {
          const cols = (bindings.knowledge_collections as Array<Record<string, unknown>>)
            .map((c) => String(c.id ?? ""))
            .filter(Boolean);
          setAvailableKnowledgeIds(cols);
        }
      } catch {
        // Keep starter fixture when offline or in isolated test mode
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [agentId, disableInitialFetch, initialGraph]);

  const validation = useMemo(() => validateFlowGraphClient(graph), [graph]);

  const availableVariables = useMemo(() => {
    const vars = new Set<string>([
      "caller_number",
      "from_number",
      "to_number",
      "call_id",
      "last_user_utterance",
      "last_tool_status",
      ...Object.keys(graph.initial_variables ?? {}),
    ]);
    for (const v of graph.variables ?? []) {
      if (v.name) vars.add(v.name);
    }
    for (const n of graph.nodes) {
      for (const v of n.variables ?? []) {
        if (v.name) vars.add(v.name);
      }
      if (n.type === "function" && typeof n.params?.output_variable === "string") {
        vars.add(n.params.output_variable);
      }
    }
    return Array.from(vars);
  }, [graph]);

  const scheduleAutosave = useCallback(
    (nextGraph: FlowGraph, etag: string) => {
      if (disableInitialFetch) return;
      if (autosaveTimerRef.current) clearTimeout(autosaveTimerRef.current);
      autosaveTimerRef.current = setTimeout(async () => {
        setSaveState("saving");
        try {
          const res = await api.putAgentFlow(agentId, {
            mode: "flow",
            flow: nextGraph as unknown as Record<string, unknown>,
            expected_etag: etag || null,
          });
          if (typeof res.draft_etag === "string") {
            setDraftEtag(res.draft_etag);
          }
          setSaveState("saved");
        } catch {
          setSaveState("error");
        }
      }, 450);
    },
    [agentId, disableInitialFetch],
  );

  const commitGraphUpdate = useCallback(
    (updater: (prev: FlowGraph) => FlowGraph) => {
      setGraph((prev) => {
        const next = updater(prev);
        setPast((h) => [...h.slice(-29), prev]);
        setFuture([]);
        scheduleAutosave(next, draftEtag);
        return next;
      });
    },
    [draftEtag, scheduleAutosave],
  );

  const handleUndo = () => {
    if (past.length === 0) return;
    const previous = past[past.length - 1];
    setPast((p) => p.slice(0, -1));
    setFuture((f) => [graph, ...f]);
    setGraph(previous);
  };

  const handleRedo = () => {
    if (future.length === 0) return;
    const next = future[0];
    setFuture((f) => f.slice(1));
    setPast((p) => [...p, graph]);
    setGraph(next);
  };

  const handleAddNode = useCallback(
    (type: FlowNodeType, position?: { x: number; y: number }) => {
      const meta = NODE_PALETTE_ITEMS.find((i) => i.type === type);
      const uniqueSuffix = `${graph.nodes.length + 1}_${Math.random()
        .toString(36)
        .slice(2, 6)}`;
      const newId = `node_${type}_${uniqueSuffix}`;
      const newNode: FlowNode = {
        id: newId,
        type,
        label: meta ? `${meta.title} ${graph.nodes.length + 1}` : newId,
        params: meta ? { ...meta.defaultParams } : {},
        position: position ?? {
          x: 120 + (graph.nodes.length % 4) * 260,
          y: 160 + Math.floor(graph.nodes.length / 4) * 150,
        },
        is_global: false,
        global_config: null,
        model_override: null,
        voice_override: null,
        variables: [],
        max_retries: 1,
        timeout_ms: 10000,
      };
      commitGraphUpdate((prev) => ({
        ...prev,
        nodes: [...prev.nodes, newNode],
      }));
      setSelectedNodeId(newId);
    },
    [commitGraphUpdate, graph.nodes.length],
  );

  const handleConnectNodes = useCallback(
    (sourceId: string, targetId: string) => {
      if (!sourceId || !targetId || sourceId === targetId) return;
      commitGraphUpdate((prev) => {
        const edgeId = `e_${sourceId}_${targetId}`;
        if (prev.edges.some((e) => e.id === edgeId)) {
          return prev;
        }
        const newEdge: FlowEdge = {
          id: edgeId,
          source: sourceId,
          target: targetId,
          source_id: sourceId,
          target_id: targetId,
          condition_label: "always",
          expression: null,
          priority: 0,
          condition: {
            kind: "always",
            equations: [],
            match_mode: "all",
            prompt: null,
            expression: null,
            label: "always",
          },
        };
        return {
          ...prev,
          edges: [...prev.edges, newEdge],
        };
      });
    },
    [commitGraphUpdate],
  );

  const handleRFConnect = useCallback(
    (connection: Connection) => {
      if (connection.source && connection.target) {
        handleConnectNodes(connection.source, connection.target);
      }
    },
    [handleConnectNodes],
  );

  const handleDeleteNode = useCallback(
    (nodeId: string) => {
      commitGraphUpdate((prev) => ({
        ...prev,
        nodes: prev.nodes.filter((n) => n.id !== nodeId),
        edges: prev.edges.filter(
          (e) => e.source_id !== nodeId && e.target_id !== nodeId,
        ),
      }));
      setSelectedNodeId((prevSelected) =>
        prevSelected === nodeId ? null : prevSelected,
      );
    },
    [commitGraphUpdate],
  );

  const handleUpdateNode = useCallback(
    (updated: FlowNode) => {
      commitGraphUpdate((prev) => ({
        ...prev,
        nodes: prev.nodes.map((n) => (n.id === updated.id ? updated : n)),
      }));
    },
    [commitGraphUpdate],
  );

  const handleUpdateEdge = useCallback(
    (updated: FlowEdge) => {
      commitGraphUpdate((prev) => ({
        ...prev,
        edges: prev.edges.map((e) => (e.id === updated.id ? updated : e)),
      }));
    },
    [commitGraphUpdate],
  );

  const handleDeleteEdge = useCallback(
    (edgeId: string) => {
      commitGraphUpdate((prev) => ({
        ...prev,
        edges: prev.edges.filter((e) => e.id !== edgeId),
      }));
    },
    [commitGraphUpdate],
  );

  const handleAutoLayout = () => {
    commitGraphUpdate((prev) => autoLayoutFlowGraph(prev, "LR"));
  };

  const handleManualSave = async () => {
    setSaveState("saving");
    setErrorBanner(null);
    try {
      const res = await api.putAgentFlow(agentId, {
        mode: "flow",
        flow: graph as unknown as Record<string, unknown>,
        expected_etag: draftEtag || null,
      });
      if (typeof res.draft_etag === "string") {
        setDraftEtag(res.draft_etag);
      }
      setSaveState("saved");
      setStatusBanner("Draft flow saved.");
    } catch (err) {
      setSaveState("error");
      setErrorBanner(err instanceof Error ? err.message : "Failed to save draft flow");
    }
  };

  const handlePublishFlow = async () => {
    setErrorBanner(null);
    setStatusBanner(null);
    if (!validation.valid) {
      setErrorBanner("Cannot publish flow while validation errors remain.");
      return;
    }
    try {
      await api.putAgentFlow(agentId, {
        mode: "flow",
        flow: graph as unknown as Record<string, unknown>,
        expected_etag: draftEtag || null,
      });
      const published = await api.publishAgentFlow(agentId, {
        changelog: "Published from visual flow canvas",
        environment: "production",
      });
      setStatusBanner(
        `Published conversation flow v${String(published.version ?? published.version_number ?? 1)}!`,
      );
    } catch (err) {
      setErrorBanner(err instanceof Error ? err.message : "Failed to publish flow");
    }
  };

  const selectedNode = useMemo(
    () => graph.nodes.find((n) => n.id === selectedNodeId) ?? null,
    [graph.nodes, selectedNodeId],
  );

  const selectedOutgoingEdges = useMemo(
    () => graph.edges.filter((e) => e.source_id === selectedNodeId),
    [graph.edges, selectedNodeId],
  );

  const errorsByNode = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const err of validation.errors) {
      if (err.node_id) {
        const list = map.get(err.node_id) ?? [];
        list.push(err.message);
        map.set(err.node_id, list);
      }
    }
    return map;
  }, [validation.errors]);

  const rfNodes: RFNode[] = useMemo(
    () =>
      graph.nodes.map((node) => ({
        id: node.id,
        type: "flowNode",
        position: node.position ?? { x: 0, y: 0 },
        data: {
          node,
          selected: node.id === selectedNodeId,
          hasError: errorsByNode.has(node.id),
          errorMessages: errorsByNode.get(node.id) ?? [],
          onSelectNode: (nid: string) => setSelectedNodeId(nid),
          onDeleteNode: handleDeleteNode,
        },
      })),
    [errorsByNode, graph.nodes, handleDeleteNode, selectedNodeId],
  );

  const rfEdges: RFEdge[] = useMemo(
    () =>
      graph.edges.map((edge) => ({
        id: edge.id,
        source: edge.source_id,
        target: edge.target_id,
        label: edge.condition_label || "always",
        animated: edge.condition?.kind === "prompt",
      })),
    [graph.edges],
  );

  return (
    <div
      data-testid="flow-editor-workspace"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "1rem",
        padding: "1.25rem",
        minHeight: "92vh",
      }}
    >
      {/* Top Toolbar */}
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: "0.75rem",
          padding: "0.85rem 1.1rem",
          borderRadius: "0.75rem",
          background: "rgba(15, 23, 42, 0.86)",
          border: "1px solid rgba(148, 163, 184, 0.2)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
          <Link
            href={`/dashboard/agents/${encodeURIComponent(agentId)}`}
            style={{
              padding: "0.35rem 0.65rem",
              borderRadius: "0.4rem",
              border: "1px solid rgba(148, 163, 184, 0.25)",
              color: "#cbd5e1",
              fontSize: "0.76rem",
              textDecoration: "none",
            }}
          >
            ← Agent Detail
          </Link>
          <div>
            <div style={{ fontSize: "0.95rem", fontWeight: 700, color: "#f8fafc" }}>
              Conversation-Flow Builder · {agentId}
            </div>
            <div style={{ fontSize: "0.72rem", color: "#94a3b8" }}>
              {graph.nodes.length} nodes · {graph.edges.length} edges · Autosave:{" "}
              <span data-testid="flow-autosave-state">{saveState}</span>
            </div>
          </div>
        </div>

        {/* Accessible Quick-Connect Controls & History */}
        <div style={{ display: "flex", alignItems: "center", gap: "0.45rem", flexWrap: "wrap" }}>
          <select
            aria-label="Connect Source Node"
            data-testid="quick-connect-source"
            value={connectSourceId}
            onChange={(e) => setConnectSourceId(e.target.value)}
            style={{
              padding: "0.35rem 0.5rem",
              borderRadius: "0.4rem",
              background: "rgba(15, 23, 42, 0.9)",
              border: "1px solid rgba(148, 163, 184, 0.25)",
              color: "#e2e8f0",
              fontSize: "0.74rem",
            }}
          >
            <option value="">Source node...</option>
            {graph.nodes.map((n) => (
              <option key={n.id} value={n.id}>
                {n.label || n.id} ({n.type})
              </option>
            ))}
          </select>
          <span style={{ color: "#64748b", fontSize: "0.75rem" }}>→</span>
          <select
            aria-label="Connect Target Node"
            data-testid="quick-connect-target"
            value={connectTargetId}
            onChange={(e) => setConnectTargetId(e.target.value)}
            style={{
              padding: "0.35rem 0.5rem",
              borderRadius: "0.4rem",
              background: "rgba(15, 23, 42, 0.9)",
              border: "1px solid rgba(148, 163, 184, 0.25)",
              color: "#e2e8f0",
              fontSize: "0.74rem",
            }}
          >
            <option value="">Target node...</option>
            {graph.nodes.map((n) => (
              <option key={n.id} value={n.id}>
                {n.label || n.id} ({n.type})
              </option>
            ))}
          </select>
          <button
            type="button"
            data-testid="quick-connect-btn"
            onClick={() => {
              if (connectSourceId && connectTargetId) {
                handleConnectNodes(connectSourceId, connectTargetId);
              }
            }}
            style={{
              padding: "0.38rem 0.65rem",
              borderRadius: "0.4rem",
              border: "1px solid rgba(99, 102, 241, 0.45)",
              background: "rgba(99, 102, 241, 0.2)",
              color: "#c7d2fe",
              fontSize: "0.74rem",
              cursor: "pointer",
            }}
          >
            Connect Nodes
          </button>

          <button
            type="button"
            data-testid="flow-undo-btn"
            disabled={past.length === 0}
            onClick={handleUndo}
            style={{
              padding: "0.38rem 0.65rem",
              borderRadius: "0.4rem",
              border: "1px solid rgba(148, 163, 184, 0.25)",
              background: "rgba(30, 41, 59, 0.7)",
              color: past.length === 0 ? "#475569" : "#e2e8f0",
              fontSize: "0.74rem",
              cursor: past.length === 0 ? "not-allowed" : "pointer",
            }}
          >
            Undo
          </button>
          <button
            type="button"
            data-testid="flow-redo-btn"
            disabled={future.length === 0}
            onClick={handleRedo}
            style={{
              padding: "0.38rem 0.65rem",
              borderRadius: "0.4rem",
              border: "1px solid rgba(148, 163, 184, 0.25)",
              background: "rgba(30, 41, 59, 0.7)",
              color: future.length === 0 ? "#475569" : "#e2e8f0",
              fontSize: "0.74rem",
              cursor: future.length === 0 ? "not-allowed" : "pointer",
            }}
          >
            Redo
          </button>
          <button
            type="button"
            data-testid="flow-autolayout-btn"
            onClick={handleAutoLayout}
            style={{
              padding: "0.38rem 0.65rem",
              borderRadius: "0.4rem",
              border: "1px solid rgba(148, 163, 184, 0.25)",
              background: "rgba(30, 41, 59, 0.7)",
              color: "#e2e8f0",
              fontSize: "0.74rem",
              cursor: "pointer",
            }}
          >
            Auto-Layout
          </button>
          <button
            type="button"
            data-testid="flow-save-btn"
            onClick={() => void handleManualSave()}
            style={{
              padding: "0.38rem 0.75rem",
              borderRadius: "0.4rem",
              border: "1px solid rgba(99, 102, 241, 0.5)",
              background: "rgba(99, 102, 241, 0.25)",
              color: "#e0e7ff",
              fontSize: "0.75rem",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Save Draft
          </button>
          <button
            type="button"
            data-testid="flow-publish-btn"
            disabled={!validation.valid}
            onClick={() => void handlePublishFlow()}
            style={{
              padding: "0.38rem 0.85rem",
              borderRadius: "0.4rem",
              border: "1px solid rgba(16, 185, 129, 0.5)",
              background: validation.valid
                ? "linear-gradient(135deg, #10b981, #059669)"
                : "rgba(30, 41, 59, 0.5)",
              color: validation.valid ? "#022c22" : "#64748b",
              fontSize: "0.75rem",
              fontWeight: 700,
              cursor: validation.valid ? "pointer" : "not-allowed",
            }}
          >
            Publish Flow
          </button>
        </div>
      </header>

      {errorBanner && (
        <div
          role="alert"
          data-testid="flow-error-banner"
          style={{
            padding: "0.65rem 0.9rem",
            borderRadius: "0.55rem",
            background: "rgba(239, 68, 68, 0.16)",
            border: "1px solid rgba(239, 68, 68, 0.45)",
            color: "#fca5a5",
            fontSize: "0.8rem",
          }}
        >
          {errorBanner}
        </div>
      )}

      {statusBanner && (
        <div
          role="status"
          data-testid="flow-status-banner"
          style={{
            padding: "0.65rem 0.9rem",
            borderRadius: "0.55rem",
            background: "rgba(16, 185, 129, 0.16)",
            border: "1px solid rgba(16, 185, 129, 0.45)",
            color: "#6ee7b7",
            fontSize: "0.8rem",
          }}
        >
          {statusBanner}
        </div>
      )}

      {/* Main 3-Column Studio: Palette | Canvas + Accessible Node Cards | Inspector */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "235px 1fr 355px",
          gap: "1rem",
          alignItems: "start",
        }}
      >
        <Palette onAddNode={(type) => handleAddNode(type)} />

        <div style={{ display: "flex", flexDirection: "column", gap: "0.85rem" }}>
          {/* Interactive React Flow Canvas */}
          <div
            data-testid="react-flow-canvas-container"
            onDragOver={(e) => {
              e.preventDefault();
              e.dataTransfer.dropEffect = "move";
            }}
            onDrop={(e) => {
              e.preventDefault();
              const droppedType = e.dataTransfer.getData(
                "application/voxdesk-flow-node",
              ) as FlowNodeType;
              if (droppedType) {
                handleAddNode(droppedType, { x: 240, y: 200 });
              }
            }}
            style={{
              height: "500px",
              borderRadius: "0.75rem",
              background: "rgba(2, 6, 23, 0.85)",
              border: "1px solid rgba(148, 163, 184, 0.2)",
              overflow: "hidden",
              position: "relative",
            }}
          >
            {typeof ResizeObserver !== "undefined" ? (
              <ReactFlow
                nodes={rfNodes}
                edges={rfEdges}
                nodeTypes={NODE_TYPES}
                onConnect={handleRFConnect}
                onNodeClick={(_, node) => setSelectedNodeId(node.id)}
                fitView
              >
                <Background />
                <Controls />
                <MiniMap />
              </ReactFlow>
            ) : null}

            {/* Accessible deterministic node list overlay / fallback for keyboard & DOM testing */}
            <div
              aria-label="Canvas Nodes"
              data-testid="canvas-nodes-list"
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: "0.65rem",
                padding: "0.75rem",
                position: typeof ResizeObserver !== "undefined" ? "absolute" : "static",
                bottom: 0,
                left: 0,
                right: 0,
                background: "rgba(2, 6, 23, 0.72)",
                borderTop: "1px solid rgba(148, 163, 184, 0.16)",
                maxHeight: "190px",
                overflowY: "auto",
              }}
            >
              {graph.nodes.map((node) => (
                <FlowNodeCardView
                  key={node.id}
                  node={node}
                  selected={node.id === selectedNodeId}
                  hasError={errorsByNode.has(node.id)}
                  errorMessages={errorsByNode.get(node.id) ?? []}
                  onSelectNode={(nid) => setSelectedNodeId(nid)}
                  onDeleteNode={handleDeleteNode}
                  includeHandles={false}
                />
              ))}
            </div>
          </div>

          {/* Live Validation Panel */}
          <ValidationPanel
            validation={validation}
            onFocusNode={(nodeId) => setSelectedNodeId(nodeId)}
          />
        </div>

        <Inspector
          node={selectedNode}
          outgoingEdges={selectedOutgoingEdges}
          availableTools={availableTools}
          availableKnowledgeIds={availableKnowledgeIds}
          availableVariables={availableVariables}
          onChangeNode={handleUpdateNode}
          onDeleteNode={handleDeleteNode}
          onChangeEdge={handleUpdateEdge}
          onDeleteEdge={handleDeleteEdge}
        />
      </div>
    </div>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export default function AgentFlowBuilderPage(props: any = {}) {
  const params = useParams<{ id: string }>();
  const resolvedAgentId = String(
    props?.agentId ?? props?.params?.id ?? params?.id ?? "default",
  );
  return (
    <FlowEditorWorkspace
      agentId={resolvedAgentId}
      initialGraph={props?.initialGraph}
      disableInitialFetch={Boolean(props?.disableInitialFetch)}
    />
  );
}
