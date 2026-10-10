// File: dashboard-next/components/flow/ValidationPanel.tsx — Validation summary and click-to-focus issue panel for conversation flows (Part 5 / Gate G6)

"use client";

import React from "react";
import { FlowValidationIssue, FlowValidationResult } from "@/lib/flow-schema";

export interface ValidationPanelProps {
  validation: FlowValidationResult;
  onFocusNode?: (nodeId: string) => void;
}

export function ValidationPanel({ validation, onFocusNode }: ValidationPanelProps) {
  const { valid, errors, warnings } = validation;

  return (
    <section
      aria-label="Flow Validation Panel"
      data-testid="flow-validation-panel"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "0.5rem",
        padding: "0.85rem 1rem",
        borderRadius: "0.75rem",
        background: "rgba(15, 23, 42, 0.85)",
        border: valid
          ? "1px solid rgba(16, 185, 129, 0.35)"
          : "1px solid rgba(239, 68, 68, 0.45)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <span
            data-testid="flow-validation-status"
            style={{
              padding: "0.18rem 0.55rem",
              borderRadius: "999px",
              fontSize: "0.72rem",
              fontWeight: 700,
              background: valid ? "rgba(16, 185, 129, 0.2)" : "rgba(239, 68, 68, 0.22)",
              color: valid ? "#6ee7b7" : "#fca5a5",
            }}
          >
            {valid ? "VALID FLOW" : `${errors.length} ERROR(S)`}
          </span>
          {warnings.length > 0 && (
            <span
              style={{
                padding: "0.18rem 0.55rem",
                borderRadius: "999px",
                fontSize: "0.72rem",
                fontWeight: 600,
                background: "rgba(245, 158, 11, 0.2)",
                color: "#fcd34d",
              }}
            >
              {warnings.length} WARNING(S)
            </span>
          )}
        </div>
        <span style={{ fontSize: "0.72rem", color: "#94a3b8" }}>
          {valid
            ? "All reachability, dead-end, and variable checks passed."
            : "Click an issue below to focus the offending node on the canvas."}
        </span>
      </div>

      {(errors.length > 0 || warnings.length > 0) && (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
          {[...errors, ...warnings].map((issue: FlowValidationIssue, idx: number) => {
            const isError = issue.severity === "error";
            return (
              <button
                key={`${issue.code}-${issue.node_id ?? "graph"}-${idx}`}
                type="button"
                data-testid={`validation-issue-${issue.code}-${issue.node_id ?? "root"}`}
                onClick={() => {
                  if (issue.node_id && onFocusNode) {
                    onFocusNode(issue.node_id);
                  }
                }}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: "0.75rem",
                  padding: "0.45rem 0.65rem",
                  borderRadius: "0.45rem",
                  border: isError
                    ? "1px solid rgba(239, 68, 68, 0.35)"
                    : "1px solid rgba(245, 158, 11, 0.35)",
                  background: isError
                    ? "rgba(239, 68, 68, 0.1)"
                    : "rgba(245, 158, 11, 0.08)",
                  color: "#f8fafc",
                  textAlign: "left",
                  cursor: issue.node_id ? "pointer" : "default",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                  <span
                    style={{
                      fontSize: "0.68rem",
                      fontWeight: 700,
                      padding: "0.1rem 0.4rem",
                      borderRadius: "0.25rem",
                      background: isError
                        ? "rgba(239, 68, 68, 0.25)"
                        : "rgba(245, 158, 11, 0.25)",
                      color: isError ? "#fecaca" : "#fde68a",
                    }}
                  >
                    {issue.code}
                  </span>
                  <span style={{ fontSize: "0.78rem", color: "#e2e8f0" }}>
                    {issue.message}
                  </span>
                </div>
                {issue.node_id && (
                  <span
                    style={{
                      fontSize: "0.7rem",
                      color: "#a5b4fc",
                      fontWeight: 600,
                      flexShrink: 0,
                    }}
                  >
                    Focus {issue.node_id} →
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}
