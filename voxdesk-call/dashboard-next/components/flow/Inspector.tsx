// File: dashboard-next/components/flow/Inspector.tsx — Right-hand node inspector for prompt, tools, dynamic variables, global node rules, and per-node LLM/voice overrides (Part 5 / Gate G6)

"use client";

import React from "react";
import { FlowEdge, FlowNode, VariableSpec } from "@/lib/flow-schema";
import { EdgeConditionEditor } from "@/components/flow/EdgeCondition";

export interface InspectorProps {
  node: FlowNode | null;
  outgoingEdges: FlowEdge[];
  availableTools: string[];
  availableKnowledgeIds: string[];
  availableVariables: string[];
  onChangeNode: (updated: FlowNode) => void;
  onDeleteNode: (nodeId: string) => void;
  onChangeEdge: (updated: FlowEdge) => void;
  onDeleteEdge: (edgeId: string) => void;
}

export function Inspector({
  node,
  outgoingEdges,
  availableTools,
  availableKnowledgeIds,
  availableVariables,
  onChangeNode,
  onDeleteNode,
  onChangeEdge,
  onDeleteEdge,
}: InspectorProps) {
  if (!node) {
    return (
      <aside
        aria-label="Node Inspector"
        data-testid="flow-inspector-empty"
        style={{
          padding: "1rem",
          borderRadius: "0.75rem",
          background: "rgba(15, 23, 42, 0.78)",
          border: "1px solid rgba(148, 163, 184, 0.16)",
          color: "#94a3b8",
          fontSize: "0.8rem",
          minWidth: "310px",
        }}
      >
        Select a node on the canvas to configure its prompt, tools, dynamic variables, model/voice overrides, or outgoing edge conditions.
      </aside>
    );
  }

  const params = node.params ?? {};

  const updateParams = (patch: Record<string, unknown>) => {
    onChangeNode({
      ...node,
      params: {
        ...params,
        ...patch,
      },
    });
  };

  const toggleTool = (toolName: string) => {
    const currentTools = Array.isArray(params.tools)
      ? (params.tools as string[])
      : [];
    const nextTools = currentTools.includes(toolName)
      ? currentTools.filter((t) => t !== toolName)
      : [...currentTools, toolName];
    updateParams({ tools: nextTools });
  };

  const addVariableSpec = () => {
    const nextVars: VariableSpec[] = [
      ...(node.variables ?? []),
      {
        name: `var_${(node.variables?.length ?? 0) + 1}`,
        type: "string",
        description: "",
        required: false,
        default: null,
        pattern: null,
      },
    ];
    onChangeNode({ ...node, variables: nextVars });
  };

  const inputStyle: React.CSSProperties = {
    width: "100%",
    padding: "0.45rem 0.6rem",
    borderRadius: "0.45rem",
    border: "1px solid rgba(148, 163, 184, 0.24)",
    background: "rgba(15, 23, 42, 0.9)",
    color: "#f8fafc",
    fontSize: "0.78rem",
  };

  const sectionTitleStyle: React.CSSProperties = {
    fontSize: "0.72rem",
    fontWeight: 700,
    textTransform: "uppercase",
    letterSpacing: "0.06em",
    color: "#94a3b8",
    marginTop: "0.4rem",
  };

  return (
    <aside
      aria-label="Node Inspector"
      data-testid="flow-inspector"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "0.75rem",
        padding: "1rem",
        borderRadius: "0.75rem",
        background: "rgba(15, 23, 42, 0.82)",
        border: "1px solid rgba(148, 163, 184, 0.2)",
        minWidth: "330px",
        maxWidth: "380px",
        maxHeight: "82vh",
        overflowY: "auto",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <div style={{ fontSize: "0.7rem", color: "#94a3b8", textTransform: "uppercase" }}>
            Node Inspector · {node.type}
          </div>
          <div style={{ fontSize: "0.95rem", fontWeight: 700, color: "#f8fafc" }}>
            {node.label || node.id}
          </div>
        </div>
        <button
          type="button"
          data-testid="inspector-delete-node"
          onClick={() => onDeleteNode(node.id)}
          style={{
            padding: "0.3rem 0.6rem",
            borderRadius: "0.4rem",
            border: "1px solid rgba(239, 68, 68, 0.4)",
            background: "rgba(239, 68, 68, 0.14)",
            color: "#fca5a5",
            fontSize: "0.74rem",
            cursor: "pointer",
          }}
        >
          Delete Node
        </button>
      </div>

      <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.74rem", color: "#cbd5e1" }}>
        Node Label
        <input
          type="text"
          aria-label="Node Label"
          data-testid="inspector-node-label"
          value={node.label}
          onChange={(e) => onChangeNode({ ...node, label: e.target.value })}
          style={inputStyle}
        />
      </label>

      {node.type === "start" && (
        <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.74rem", color: "#cbd5e1" }}>
          Opening Greeting
          <textarea
            rows={3}
            aria-label="Opening Greeting"
            data-testid="inspector-greeting-input"
            value={String(params.greeting ?? "")}
            onChange={(e) => updateParams({ greeting: e.target.value })}
            style={inputStyle}
          />
        </label>
      )}

      {node.type === "conversation" && (
        <>
          <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.74rem", color: "#cbd5e1" }}>
            Node Prompt (supports {"{{variable}}"} interpolation)
            <textarea
              rows={4}
              aria-label="Node Prompt"
              data-testid="inspector-prompt-input"
              value={String(params.prompt ?? "")}
              onChange={(e) => updateParams({ prompt: e.target.value })}
              style={inputStyle}
            />
          </label>

          <div style={sectionTitleStyle}>Node Tools ({Array.isArray(params.tools) ? params.tools.length : 0})</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "0.35rem" }}>
            {availableTools.map((toolName) => {
              const active = Array.isArray(params.tools) && params.tools.includes(toolName);
              return (
                <button
                  key={toolName}
                  type="button"
                  data-testid={`inspector-tool-toggle-${toolName}`}
                  onClick={() => toggleTool(toolName)}
                  style={{
                    padding: "0.25rem 0.55rem",
                    borderRadius: "999px",
                    fontSize: "0.72rem",
                    border: active
                      ? "1px solid #6366f1"
                      : "1px solid rgba(148, 163, 184, 0.25)",
                    background: active ? "rgba(99, 102, 241, 0.25)" : "rgba(30, 41, 59, 0.6)",
                    color: active ? "#e0e7ff" : "#94a3b8",
                    cursor: "pointer",
                  }}
                >
                  {active ? "✓ " : "+ "}
                  {toolName}
                </button>
              );
            })}
          </div>

          {availableKnowledgeIds.length > 0 && (
            <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.74rem", color: "#cbd5e1" }}>
              Knowledge Collection IDs (comma-separated)
              <input
                type="text"
                value={
                  Array.isArray(params.knowledge_collection_ids)
                    ? params.knowledge_collection_ids.join(", ")
                    : ""
                }
                onChange={(e) =>
                  updateParams({
                    knowledge_collection_ids: e.target.value
                      .split(",")
                      .map((s) => s.trim())
                      .filter(Boolean),
                  })
                }
                placeholder={availableKnowledgeIds.join(", ")}
                style={inputStyle}
              />
            </label>
          )}
        </>
      )}

      {node.type === "function" && (
        <>
          <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.74rem", color: "#cbd5e1" }}>
            Function / AgentTool Name
            <input
              type="text"
              aria-label="Function Tool Name"
              data-testid="inspector-function-tool-name"
              value={String(params.tool_name ?? "")}
              onChange={(e) => updateParams({ tool_name: e.target.value })}
              style={inputStyle}
            />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.74rem", color: "#cbd5e1" }}>
            Output Variable Name
            <input
              type="text"
              aria-label="Output Variable Name"
              data-testid="inspector-function-output-var"
              value={String(params.output_variable ?? "tool_output")}
              onChange={(e) => updateParams({ output_variable: e.target.value })}
              style={inputStyle}
            />
          </label>
        </>
      )}

      {node.type === "transfer" && (
        <>
          <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.74rem", color: "#cbd5e1" }}>
            Transfer Mode
            <select
              aria-label="Transfer Mode"
              data-testid="inspector-transfer-mode"
              value={String(params.transfer_mode ?? "warm")}
              onChange={(e) => updateParams({ transfer_mode: e.target.value })}
              style={inputStyle}
            >
              <option value="warm">Warm Transfer (Brief Human Agent)</option>
              <option value="cold">Cold Transfer (Blind Dial)</option>
            </select>
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.74rem", color: "#cbd5e1" }}>
            Destination E.164 / Queue
            <input
              type="text"
              aria-label="Transfer Destination"
              data-testid="inspector-transfer-destination"
              value={String(params.destination ?? "")}
              onChange={(e) => updateParams({ destination: e.target.value })}
              style={inputStyle}
            />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.74rem", color: "#cbd5e1" }}>
            Whisper Briefing
            <input
              type="text"
              aria-label="Whisper Briefing"
              value={String(params.whisper_text ?? "")}
              onChange={(e) => updateParams({ whisper_text: e.target.value })}
              style={inputStyle}
            />
          </label>
        </>
      )}

      {node.type === "press_digit" && (
        <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.74rem", color: "#cbd5e1" }}>
          DTMF Digits
          <input
            type="text"
            aria-label="DTMF Digits"
            data-testid="inspector-dtmf-digits"
            value={String(params.digits ?? "1")}
            onChange={(e) => updateParams({ digits: e.target.value })}
            style={inputStyle}
          />
        </label>
      )}

      {node.type === "send_sms" && (
        <>
          <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.74rem", color: "#cbd5e1" }}>
            Recipient Number
            <input
              type="text"
              aria-label="SMS Recipient"
              value={String(params.to_number ?? "{{caller_number}}")}
              onChange={(e) => updateParams({ to_number: e.target.value })}
              style={inputStyle}
            />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.74rem", color: "#cbd5e1" }}>
            SMS Body
            <textarea
              rows={3}
              aria-label="SMS Body"
              value={String(params.message ?? "")}
              onChange={(e) => updateParams({ message: e.target.value })}
              style={inputStyle}
            />
          </label>
        </>
      )}

      {node.type === "subagent" && (
        <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.74rem", color: "#cbd5e1" }}>
          Target Subagent ID
          <input
            type="text"
            aria-label="Target Subagent ID"
            value={String(params.target_agent_id ?? "")}
            onChange={(e) => updateParams({ target_agent_id: e.target.value })}
            style={inputStyle}
          />
        </label>
      )}

      {node.type === "end" && (
        <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.74rem", color: "#cbd5e1" }}>
          Closing Spoken Line
          <textarea
            rows={2}
            aria-label="Closing Spoken Line"
            value={String(params.speak_text ?? "")}
            onChange={(e) => updateParams({ speak_text: e.target.value })}
            style={inputStyle}
          />
        </label>
      )}

      {/* Global node configuration */}
      <div style={sectionTitleStyle}>Global Node Trigger</div>
      <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", fontSize: "0.76rem", color: "#e2e8f0" }}>
        <input
          type="checkbox"
          data-testid="inspector-is-global"
          checked={Boolean(node.is_global)}
          onChange={(e) => {
            const enabled = e.target.checked;
            onChangeNode({
              ...node,
              is_global: enabled,
              global_config: enabled
                ? {
                    enabled: true,
                    return_to_previous: node.global_config?.return_to_previous ?? false,
                    trigger_condition: node.global_config?.trigger_condition ?? {
                      kind: "prompt",
                      equations: [],
                      match_mode: "all",
                      prompt: "Caller asks to speak to a human operator",
                      expression: null,
                      label: "Global trigger",
                    },
                  }
                : null,
            });
          }}
        />
        Allow entry from any node (Global Node)
      </label>
      {node.is_global && (
        <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.74rem", color: "#cbd5e1" }}>
          Global Trigger Prompt
          <input
            type="text"
            aria-label="Global Trigger Prompt"
            data-testid="inspector-global-prompt"
            value={node.global_config?.trigger_condition?.prompt ?? ""}
            onChange={(e) =>
              onChangeNode({
                ...node,
                global_config: {
                  enabled: true,
                  return_to_previous: node.global_config?.return_to_previous ?? false,
                  trigger_condition: {
                    kind: "prompt",
                    equations: [],
                    match_mode: "all",
                    prompt: e.target.value,
                    expression: null,
                    label: "Global trigger",
                  },
                },
              })
            }
            style={inputStyle}
          />
        </label>
      )}

      {/* Per-node Model & Voice overrides */}
      <div style={sectionTitleStyle}>Per-Node Model & Voice Overrides</div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.45rem" }}>
        <input
          type="text"
          aria-label="Model Override"
          data-testid="inspector-model-override"
          placeholder="LLM model override"
          value={node.model_override?.model ?? ""}
          onChange={(e) =>
            onChangeNode({
              ...node,
              model_override: e.target.value
                ? {
                    provider: node.model_override?.provider ?? "groq",
                    model: e.target.value,
                    temperature: node.model_override?.temperature ?? 0.2,
                    max_tokens: node.model_override?.max_tokens ?? 256,
                  }
                : null,
            })
          }
          style={inputStyle}
        />
        <input
          type="text"
          aria-label="Voice Override"
          data-testid="inspector-voice-override"
          placeholder="Voice ID override"
          value={node.voice_override?.voice_id ?? ""}
          onChange={(e) =>
            onChangeNode({
              ...node,
              voice_override: e.target.value
                ? {
                    provider: node.voice_override?.provider ?? "cartesia",
                    voice_id: e.target.value,
                    speed: node.voice_override?.speed ?? 1.0,
                  }
                : null,
            })
          }
          style={inputStyle}
        />
      </div>

      {/* Node Variables */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={sectionTitleStyle}>Node Variables ({node.variables?.length ?? 0})</span>
        <button
          type="button"
          data-testid="inspector-add-variable"
          onClick={addVariableSpec}
          style={{
            padding: "0.2rem 0.45rem",
            borderRadius: "0.35rem",
            border: "1px solid rgba(99, 102, 241, 0.4)",
            background: "rgba(99, 102, 241, 0.15)",
            color: "#c7d2fe",
            fontSize: "0.7rem",
            cursor: "pointer",
          }}
        >
          + Variable
        </button>
      </div>
      {(node.variables ?? []).map((v, idx) => (
        <div key={idx} style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.4rem" }}>
          <input
            type="text"
            aria-label={`Node variable ${idx} name`}
            value={v.name}
            onChange={(e) => {
              const next = [...(node.variables ?? [])];
              next[idx] = { ...v, name: e.target.value };
              onChangeNode({ ...node, variables: next });
            }}
            placeholder="variable_name"
            style={inputStyle}
          />
          <input
            type="text"
            aria-label={`Node variable ${idx} pattern`}
            value={v.pattern ?? ""}
            onChange={(e) => {
              const next = [...(node.variables ?? [])];
              next[idx] = { ...v, pattern: e.target.value || null };
              onChangeNode({ ...node, variables: next });
            }}
            placeholder="regex pattern (optional)"
            style={inputStyle}
          />
        </div>
      ))}

      {/* Outgoing Edge Conditions */}
      <div style={sectionTitleStyle}>Outgoing Edges ({outgoingEdges.length})</div>
      {outgoingEdges.length === 0 ? (
        <div style={{ fontSize: "0.74rem", color: "#64748b" }}>
          No outgoing edges from this node.
        </div>
      ) : (
        outgoingEdges.map((edge) => (
          <EdgeConditionEditor
            key={edge.id}
            edge={edge}
            availableVariables={availableVariables}
            onChange={onChangeEdge}
            onDeleteEdge={onDeleteEdge}
          />
        ))
      )}
    </aside>
  );
}
