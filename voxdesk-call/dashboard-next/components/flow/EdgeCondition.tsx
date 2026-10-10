// File: dashboard-next/components/flow/EdgeCondition.tsx — Edge condition editor supporting deterministic equation clauses and LLM prompt conditions (Part 5 / Gate G6)

"use client";

import React from "react";
import {
  EQUATION_OPERATORS,
  EdgeCondition,
  EdgeConditionKind,
  EquationClause,
  EquationOperator,
  FlowEdge,
} from "@/lib/flow-schema";

export interface EdgeConditionEditorProps {
  edge: FlowEdge;
  availableVariables: string[];
  onChange: (updated: FlowEdge) => void;
  onDeleteEdge?: (edgeId: string) => void;
}

export function EdgeConditionEditor({
  edge,
  availableVariables,
  onChange,
  onDeleteEdge,
}: EdgeConditionEditorProps) {
  const condition: EdgeCondition = edge.condition ?? {
    kind: "always",
    equations: [],
    match_mode: "all",
    prompt: null,
    expression: edge.expression ?? null,
    label: edge.condition_label || "always",
  };

  const updateCondition = (patch: Partial<EdgeCondition>, edgePatch?: Partial<FlowEdge>) => {
    const nextCondition: EdgeCondition = {
      ...condition,
      ...patch,
    };
    const nextLabel =
      edgePatch?.condition_label ??
      patch.label ??
      nextCondition.label ??
      edge.condition_label ??
      "always";
    onChange({
      ...edge,
      ...edgePatch,
      condition_label: nextLabel,
      expression: nextCondition.expression ?? null,
      condition: {
        ...nextCondition,
        label: nextLabel,
      },
    });
  };

  const handleKindChange = (kind: EdgeConditionKind) => {
    const defaultEquations: EquationClause[] =
      kind === "equation" && condition.equations.length === 0
        ? [
            {
              variable: availableVariables[0] || "department",
              operator: "==",
              value: "sales",
            },
          ]
        : condition.equations;
    updateCondition({
      kind,
      equations: defaultEquations,
      prompt:
        kind === "prompt" && !condition.prompt
          ? "Caller asks to speak to sales or account executive"
          : condition.prompt,
      label:
        kind === "always"
          ? "always"
          : kind === "else"
          ? "else"
          : condition.label === "always"
          ? kind
          : condition.label,
    });
  };

  const handleAddClause = () => {
    const nextClauses: EquationClause[] = [
      ...condition.equations,
      {
        variable: availableVariables[0] || "caller_number",
        operator: "==",
        value: "",
      },
    ];
    updateCondition({ equations: nextClauses });
  };

  const handleClauseChange = (index: number, patch: Partial<EquationClause>) => {
    const nextClauses = condition.equations.map((clause, i) =>
      i === index ? { ...clause, ...patch } : clause
    );
    updateCondition({ equations: nextClauses });
  };

  const handleRemoveClause = (index: number) => {
    const nextClauses = condition.equations.filter((_, i) => i !== index);
    updateCondition({ equations: nextClauses });
  };

  const inputStyle: React.CSSProperties = {
    width: "100%",
    padding: "0.42rem 0.55rem",
    borderRadius: "0.45rem",
    border: "1px solid rgba(148, 163, 184, 0.24)",
    background: "rgba(15, 23, 42, 0.9)",
    color: "#f8fafc",
    fontSize: "0.78rem",
  };

  return (
    <div
      data-testid={`edge-condition-editor-${edge.id}`}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "0.65rem",
        padding: "0.85rem",
        borderRadius: "0.65rem",
        background: "rgba(15, 23, 42, 0.85)",
        border: "1px solid rgba(99, 102, 241, 0.35)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ fontSize: "0.78rem", fontWeight: 700, color: "#e2e8f0" }}>
          Edge: {edge.source_id} → {edge.target_id}
        </div>
        {onDeleteEdge && (
          <button
            type="button"
            data-testid={`delete-edge-${edge.id}`}
            onClick={() => onDeleteEdge(edge.id)}
            style={{
              padding: "0.2rem 0.5rem",
              borderRadius: "0.35rem",
              border: "1px solid rgba(239, 68, 68, 0.4)",
              background: "rgba(239, 68, 68, 0.14)",
              color: "#fca5a5",
              fontSize: "0.7rem",
              cursor: "pointer",
            }}
          >
            Delete Edge
          </button>
        )}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 90px", gap: "0.5rem" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.72rem", color: "#94a3b8" }}>
          Edge Label
          <input
            type="text"
            aria-label="Edge Label"
            data-testid="edge-label-input"
            value={edge.condition_label}
            onChange={(e) =>
              updateCondition({ label: e.target.value }, { condition_label: e.target.value })
            }
            style={inputStyle}
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.72rem", color: "#94a3b8" }}>
          Priority
          <input
            type="number"
            aria-label="Edge Priority"
            data-testid="edge-priority-input"
            value={edge.priority}
            onChange={(e) =>
              updateCondition({}, { priority: Number(e.target.value) || 0 })
            }
            style={inputStyle}
          />
        </label>
      </div>

      <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.72rem", color: "#94a3b8" }}>
        Condition Type
        <select
          aria-label="Condition Type"
          data-testid="edge-condition-kind"
          value={condition.kind}
          onChange={(e) => handleKindChange(e.target.value as EdgeConditionKind)}
          style={inputStyle}
        >
          <option value="always">Always (Unconditional)</option>
          <option value="equation">Deterministic Equation Builder</option>
          <option value="prompt">LLM Judge Prompt Condition</option>
          <option value="else">Else (Fallback Branch)</option>
        </select>
      </label>

      {condition.kind === "equation" && (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <label style={{ fontSize: "0.72rem", color: "#94a3b8", display: "flex", alignItems: "center", gap: "0.4rem" }}>
              Match
              <select
                aria-label="Equation Match Mode"
                data-testid="edge-match-mode"
                value={condition.match_mode}
                onChange={(e) =>
                  updateCondition({ match_mode: e.target.value as "all" | "any" })
                }
                style={{ ...inputStyle, width: "auto", padding: "0.2rem 0.4rem" }}
              >
                <option value="all">ALL clauses (AND)</option>
                <option value="any">ANY clause (OR)</option>
              </select>
            </label>
            <button
              type="button"
              data-testid="edge-add-clause"
              onClick={handleAddClause}
              style={{
                padding: "0.22rem 0.5rem",
                borderRadius: "0.35rem",
                border: "1px solid rgba(99, 102, 241, 0.4)",
                background: "rgba(99, 102, 241, 0.16)",
                color: "#c7d2fe",
                fontSize: "0.7rem",
                cursor: "pointer",
              }}
            >
              + Add Clause
            </button>
          </div>

          {condition.equations.map((clause, idx) => (
            <div
              key={idx}
              data-testid={`edge-clause-${idx}`}
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 90px 1fr auto",
                gap: "0.35rem",
                alignItems: "center",
              }}
            >
              <input
                type="text"
                aria-label={`Clause ${idx} variable`}
                placeholder="variable"
                list={`edge-vars-${edge.id}`}
                value={clause.variable}
                onChange={(e) => handleClauseChange(idx, { variable: e.target.value })}
                style={inputStyle}
              />
              <select
                aria-label={`Clause ${idx} operator`}
                value={clause.operator}
                onChange={(e) =>
                  handleClauseChange(idx, {
                    operator: e.target.value as EquationOperator,
                  })
                }
                style={inputStyle}
              >
                {EQUATION_OPERATORS.map((op) => (
                  <option key={op} value={op}>
                    {op}
                  </option>
                ))}
              </select>
              <input
                type="text"
                aria-label={`Clause ${idx} value`}
                placeholder="value"
                disabled={clause.operator === "is_set" || clause.operator === "is_empty"}
                value={clause.value === null || clause.value === undefined ? "" : String(clause.value)}
                onChange={(e) => handleClauseChange(idx, { value: e.target.value })}
                style={inputStyle}
              />
              <button
                type="button"
                aria-label={`Remove clause ${idx}`}
                onClick={() => handleRemoveClause(idx)}
                style={{
                  border: "none",
                  background: "transparent",
                  color: "#f87171",
                  cursor: "pointer",
                  fontSize: "0.8rem",
                }}
              >
                ✕
              </button>
            </div>
          ))}
          <datalist id={`edge-vars-${edge.id}`}>
            {availableVariables.map((v) => (
              <option key={v} value={v} />
            ))}
          </datalist>
        </div>
      )}

      {condition.kind === "prompt" && (
        <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.72rem", color: "#94a3b8" }}>
          Natural-Language Judge Condition
          <textarea
            aria-label="Prompt Condition"
            data-testid="edge-prompt-input"
            rows={2}
            value={condition.prompt ?? ""}
            onChange={(e) => updateCondition({ prompt: e.target.value })}
            placeholder="e.g. Caller confirms they want to schedule an appointment"
            style={{ ...inputStyle, resize: "vertical" }}
          />
        </label>
      )}
    </div>
  );
}
