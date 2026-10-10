// File: dashboard-next/components/flow/Palette.tsx — Draggable and click-to-add conversation-flow node palette (Part 5 / Gate G6)

"use client";

import React from "react";
import { FLOW_NODE_TYPES, FlowNodeType } from "@/lib/flow-schema";

export interface NodePaletteMeta {
  type: FlowNodeType;
  title: string;
  subtitle: string;
  accent: string;
  defaultParams: Record<string, unknown>;
}

export const NODE_PALETTE_ITEMS: NodePaletteMeta[] = [
  {
    type: "start",
    title: "Start",
    subtitle: "Call entry greeting",
    accent: "#10b981",
    defaultParams: { greeting: "Hello! How can I help you today?" },
  },
  {
    type: "conversation",
    title: "Conversation",
    subtitle: "LLM prompt + node tools",
    accent: "#6366f1",
    defaultParams: {
      prompt: "Respond naturally and assist the caller.",
      tools: [],
    },
  },
  {
    type: "logic_split",
    title: "Logic Split",
    subtitle: "Equation or prompt branch",
    accent: "#f59e0b",
    defaultParams: { description: "Branch on dynamic variables or intent" },
  },
  {
    type: "function",
    title: "Function",
    subtitle: "HTTP / AgentTool invocation",
    accent: "#06b6d4",
    defaultParams: {
      tool_name: "lookup_customer",
      arguments: {},
      output_variable: "tool_output",
    },
  },
  {
    type: "transfer",
    title: "Transfer",
    subtitle: "Cold or warm handoff",
    accent: "#ec4899",
    defaultParams: {
      transfer_mode: "warm",
      destination: "+14155550199",
      whisper_text: "Incoming verified caller.",
    },
  },
  {
    type: "press_digit",
    title: "Press Digit",
    subtitle: "Send DTMF tones",
    accent: "#8b5cf6",
    defaultParams: { digits: "1", pause_ms: 250 },
  },
  {
    type: "send_sms",
    title: "Send SMS",
    subtitle: "SMS confirmation text",
    accent: "#14b8a6",
    defaultParams: {
      message: "Your appointment is confirmed.",
      to_number: "{{caller_number}}",
    },
  },
  {
    type: "extract_variables",
    title: "Extract Variables",
    subtitle: "Capture dynamic fields",
    accent: "#eab308",
    defaultParams: {
      variables: [{ name: "customer_email", type: "string", pattern: "" }],
    },
  },
  {
    type: "subagent",
    title: "Subagent",
    subtitle: "Handoff to another agent",
    accent: "#3b82f6",
    defaultParams: { target_agent_id: "", handoff_context: {} },
  },
  {
    type: "end",
    title: "End Call",
    subtitle: "Speak closing & hang up",
    accent: "#ef4444",
    defaultParams: {
      reason: "completed",
      speak_text: "Thank you for calling. Goodbye!",
    },
  },
];

export interface PaletteProps {
  onAddNode: (type: FlowNodeType) => void;
}

export function Palette({ onAddNode }: PaletteProps) {
  const handleDragStart = (event: React.DragEvent<HTMLButtonElement>, type: FlowNodeType) => {
    event.dataTransfer.setData("application/voxdesk-flow-node", type);
    event.dataTransfer.effectAllowed = "move";
  };

  return (
    <aside
      aria-label="Node Palette"
      data-testid="flow-palette"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "0.5rem",
        padding: "0.875rem",
        background: "rgba(15, 23, 42, 0.78)",
        border: "1px solid rgba(148, 163, 184, 0.16)",
        borderRadius: "0.75rem",
        minWidth: "220px",
      }}
    >
      <div style={{ fontSize: "0.75rem", fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "#94a3b8" }}>
        Node Palette ({FLOW_NODE_TYPES.length})
      </div>
      <div style={{ fontSize: "0.72rem", color: "#64748b", marginBottom: "0.25rem" }}>
        Drag onto canvas or click to add
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: "0.4rem" }}>
        {NODE_PALETTE_ITEMS.map((item) => (
          <button
            key={item.type}
            type="button"
            draggable
            data-testid={`palette-add-${item.type}`}
            onDragStart={(e) => handleDragStart(e, item.type)}
            onClick={() => onAddNode(item.type)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "0.6rem",
              padding: "0.5rem 0.65rem",
              borderRadius: "0.5rem",
              border: "1px solid rgba(148, 163, 184, 0.16)",
              background: "rgba(30, 41, 59, 0.65)",
              color: "#f8fafc",
              textAlign: "left",
              cursor: "grab",
            }}
          >
            <span
              style={{
                width: "10px",
                height: "10px",
                borderRadius: "999px",
                background: item.accent,
                flexShrink: 0,
              }}
            />
            <span style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
              <span style={{ fontSize: "0.8rem", fontWeight: 600 }}>{item.title}</span>
              <span style={{ fontSize: "0.68rem", color: "#94a3b8" }}>{item.subtitle}</span>
            </span>
          </button>
        ))}
      </div>
    </aside>
  );
}
