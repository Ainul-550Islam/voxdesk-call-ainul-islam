// File: dashboard-next/components/flow/NodeCard.tsx — Custom node renderer per conversation-flow node type (Part 5 / Gate G6)

"use client";

import React from "react";
import { Handle, Position } from "@xyflow/react";
import { FlowNode } from "@/lib/flow-schema";
import { NODE_PALETTE_ITEMS } from "@/components/flow/Palette";

export interface NodeCardData extends Record<string, unknown> {
  node: FlowNode;
  selected?: boolean;
  hasError?: boolean;
  errorMessages?: string[];
  onSelectNode?: (nodeId: string) => void;
  onDeleteNode?: (nodeId: string) => void;
}

export function getNodeSummaryText(node: FlowNode): string {
  const p = node.params ?? {};
  switch (node.type) {
    case "start":
      return String(p.greeting || "Starts the call");
    case "conversation":
      return String(p.prompt || "Conversation prompt");
    case "logic_split":
      return String(p.description || "Conditional branch evaluation");
    case "function":
      return `Tool: ${String(p.tool_name || p.name || "unbound")}`;
    case "transfer":
      return `${String(p.transfer_mode || "cold").toUpperCase()} -> ${String(p.destination || "unset")}`;
    case "press_digit":
      return `DTMF digits: ${String(p.digits || p.digit || "1")}`;
    case "send_sms":
      return `SMS: ${String(p.message || p.text || "")}`;
    case "extract_variables": {
      const vars = Array.isArray(p.variables) ? p.variables.length : 0;
      return `Extract ${vars} variable(s)`;
    }
    case "subagent":
      return `Subagent: ${String(p.target_agent_id || p.agent_id || "unset")}`;
    case "end":
      return String(p.speak_text || p.reason || "Terminate call");
    default:
      return node.type;
  }
}

export function FlowNodeCardView({
  node,
  selected = false,
  hasError = false,
  errorMessages = [],
  onSelectNode,
  onDeleteNode,
  includeHandles = false,
}: {
  node: FlowNode;
  selected?: boolean;
  hasError?: boolean;
  errorMessages?: string[];
  onSelectNode?: (nodeId: string) => void;
  onDeleteNode?: (nodeId: string) => void;
  includeHandles?: boolean;
}) {
  const paletteMeta = NODE_PALETTE_ITEMS.find((i) => i.type === node.type);
  const accent = paletteMeta?.accent ?? "#6366f1";
  const summary = getNodeSummaryText(node);
  const isGlobal = Boolean(node.is_global || node.global_config?.enabled);
  const hasModelOverride = Boolean(node.model_override?.model || node.model_override?.provider);
  const hasVoiceOverride = Boolean(node.voice_override?.voice_id || node.voice_override?.provider);

  return (
    <div
      data-testid={`flow-node-card-${node.id}`}
      data-node-type={node.type}
      onClick={() => onSelectNode?.(node.id)}
      style={{
        minWidth: "220px",
        maxWidth: "260px",
        borderRadius: "0.75rem",
        padding: "0.7rem 0.85rem",
        background: "rgba(15, 23, 42, 0.94)",
        border: hasError
          ? "2px solid #ef4444"
          : selected
          ? `2px solid ${accent}`
          : "1px solid rgba(148, 163, 184, 0.25)",
        boxShadow: selected
          ? `0 0 0 3px ${accent}33`
          : "0 8px 20px rgba(2, 6, 23, 0.45)",
        color: "#f8fafc",
        cursor: "pointer",
        position: "relative",
      }}
    >
      {includeHandles && node.type !== "start" && (
        <Handle
          type="target"
          position={Position.Left}
          style={{ background: accent, width: 10, height: 10 }}
        />
      )}

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "0.5rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.45rem" }}>
          <span
            style={{
              width: "9px",
              height: "9px",
              borderRadius: "999px",
              background: accent,
              display: "inline-block",
            }}
          />
          <span
            style={{
              fontSize: "0.68rem",
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              color: "#cbd5e1",
            }}
          >
            {paletteMeta?.title ?? node.type}
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "0.3rem" }}>
          {isGlobal && (
            <span
              data-testid={`node-global-badge-${node.id}`}
              style={{
                fontSize: "0.62rem",
                padding: "0.1rem 0.35rem",
                borderRadius: "0.25rem",
                background: "rgba(234, 179, 8, 0.2)",
                color: "#fde047",
                fontWeight: 600,
              }}
            >
              GLOBAL
            </span>
          )}
          {hasError && (
            <span
              data-testid={`node-error-badge-${node.id}`}
              title={errorMessages.join("; ")}
              style={{
                fontSize: "0.62rem",
                padding: "0.1rem 0.35rem",
                borderRadius: "0.25rem",
                background: "rgba(239, 68, 68, 0.22)",
                color: "#fca5a5",
                fontWeight: 700,
              }}
            >
              INVALID
            </span>
          )}
          {onDeleteNode && (
            <button
              type="button"
              aria-label={`Delete node ${node.id}`}
              data-testid={`delete-node-${node.id}`}
              onClick={(e) => {
                e.stopPropagation();
                onDeleteNode(node.id);
              }}
              style={{
                border: "none",
                background: "transparent",
                color: "#94a3b8",
                fontSize: "0.75rem",
                cursor: "pointer",
                padding: "0 0.2rem",
              }}
            >
              ✕
            </button>
          )}
        </div>
      </div>

      <div
        style={{
          fontSize: "0.86rem",
          fontWeight: 700,
          marginTop: "0.3rem",
          color: "#f8fafc",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {node.label || node.id}
      </div>

      <div
        style={{
          fontSize: "0.73rem",
          color: "#94a3b8",
          marginTop: "0.25rem",
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
          lineHeight: 1.35,
        }}
      >
        {summary}
      </div>

      {(hasModelOverride || hasVoiceOverride) && (
        <div style={{ display: "flex", gap: "0.35rem", marginTop: "0.45rem", flexWrap: "wrap" }}>
          {hasModelOverride && (
            <span
              style={{
                fontSize: "0.62rem",
                padding: "0.1rem 0.35rem",
                borderRadius: "0.25rem",
                background: "rgba(99, 102, 241, 0.18)",
                color: "#a5b4fc",
              }}
            >
              LLM: {node.model_override?.model || node.model_override?.provider}
            </span>
          )}
          {hasVoiceOverride && (
            <span
              style={{
                fontSize: "0.62rem",
                padding: "0.1rem 0.35rem",
                borderRadius: "0.25rem",
                background: "rgba(20, 184, 166, 0.18)",
                color: "#5eead4",
              }}
            >
              Voice: {node.voice_override?.voice_id || node.voice_override?.provider}
            </span>
          )}
        </div>
      )}

      {includeHandles && node.type !== "end" && (
        <Handle
          type="source"
          position={Position.Right}
          style={{ background: accent, width: 10, height: 10 }}
        />
      )}
    </div>
  );
}

export function NodeCard({ data }: { data: NodeCardData }) {
  return (
    <FlowNodeCardView
      node={data.node}
      selected={Boolean(data.selected)}
      hasError={Boolean(data.hasError)}
      errorMessages={data.errorMessages ?? []}
      onSelectNode={data.onSelectNode}
      onDeleteNode={data.onDeleteNode}
      includeHandles
    />
  );
}
