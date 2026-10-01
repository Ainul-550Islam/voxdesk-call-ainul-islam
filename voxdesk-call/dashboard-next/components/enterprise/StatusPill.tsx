// File: components/enterprise/StatusPill.tsx — Semantic status component for healthy, warning, degraded, disabled, active, pending and unknown states with textual/ARIA meaning

"use client";

import React from "react";
import { getSemanticStatusLabel, getStatusAriaProps } from "@/lib/enterprise-ui/accessibility";
import type { SemanticStatus } from "@/lib/enterprise-ui/accessibility";

export type StatusPillVariant = SemanticStatus | "idle";

export interface StatusPillProps extends React.HTMLAttributes<HTMLSpanElement> {
  readonly status: StatusPillVariant;
  readonly label?: string;
  readonly showDot?: boolean;
  readonly size?: "sm" | "md";
}

const variantStyles: Record<StatusPillVariant, { bg: string; color: string; border: string; dot: string }> = {
  active: {
    bg: "rgba(124, 92, 252, 0.14)",
    color: "#a78bfa",
    border: "rgba(124, 92, 252, 0.28)",
    dot: "#7c5cfc",
  },
  healthy: {
    bg: "rgba(16, 185, 129, 0.14)",
    color: "#34d399",
    border: "rgba(16, 185, 129, 0.28)",
    dot: "#10b981",
  },
  warning: {
    bg: "rgba(245, 158, 11, 0.14)",
    color: "#fbbf24",
    border: "rgba(245, 158, 11, 0.28)",
    dot: "#f59e0b",
  },
  degraded: {
    bg: "rgba(239, 68, 68, 0.14)",
    color: "#f87171",
    border: "rgba(239, 68, 68, 0.28)",
    dot: "#ef4444",
  },
  disabled: {
    bg: "rgba(255,255,255,0.06)",
    color: "var(--enterprise-text-tertiary)",
    border: "rgba(255,255,255,0.10)",
    dot: "#6b6b80",
  },
  pending: {
    bg: "rgba(245, 158, 11, 0.14)",
    color: "#fbbf24",
    border: "rgba(245, 158, 11, 0.28)",
    dot: "#f59e0b",
  },
  error: {
    bg: "rgba(239, 68, 68, 0.16)",
    color: "#f87171",
    border: "rgba(239, 68, 68, 0.32)",
    dot: "#ef4444",
  },
  unknown: {
    bg: "rgba(255,255,255,0.05)",
    color: "var(--enterprise-text-secondary)",
    border: "rgba(255,255,255,0.10)",
    dot: "var(--enterprise-text-tertiary)",
  },
  idle: {
    bg: "rgba(255,255,255,0.04)",
    color: "var(--enterprise-text-tertiary)",
    border: "rgba(255,255,255,0.08)",
    dot: "#6b6b80",
  },
};

export function StatusPill({
  status,
  label,
  showDot = true,
  size = "md",
  className,
  style,
  ...rest
}: StatusPillProps): React.ReactElement {
  const semanticLabel = label ?? getSemanticStatusLabel(status as SemanticStatus);
  const ariaProps = getStatusAriaProps(status as SemanticStatus);
  const variant = variantStyles[status] ?? variantStyles.unknown;

  return (
    <span
      className={["enterprise-status-pill", className].filter(Boolean).join(" ")}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: showDot ? 6 : 0,
        padding: size === "sm" ? "2px 8px" : "4px 10px",
        borderRadius: "var(--enterprise-radius-full)",
        background: variant.bg,
        color: variant.color,
        border: `1px solid ${variant.border}`,
        fontSize: size === "sm" ? 10 : 11,
        fontWeight: 700,
        letterSpacing: "0.02em",
        lineHeight: 1,
        backdropFilter: "blur(12px)",
        WebkitBackdropFilter: "blur(12px)",
        whiteSpace: "nowrap",
        ...style,
      }}
      {...ariaProps}
      aria-label={ariaProps["aria-label"] ?? semanticLabel}
      title={semanticLabel}
      {...rest}
    >
      {showDot && (
        <span
          aria-hidden="true"
          style={{
            width: size === "sm" ? 5 : 6,
            height: size === "sm" ? 5 : 6,
            borderRadius: "50%",
            background: variant.dot,
            boxShadow: status === "active" || status === "healthy" ? `0 0 8px ${variant.dot}` : "none",
            flexShrink: 0,
            display: "inline-block",
          }}
        />
      )}
      <span>{semanticLabel}</span>
      {/* Text alternative for color-blind users — icon already present via dot, but ensure label is always visible */}
      <span className="enterprise-sr-only">Status: {semanticLabel}</span>
    </span>
  );
}
