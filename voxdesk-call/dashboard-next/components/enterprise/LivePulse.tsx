// File: components/enterprise/LivePulse.tsx — Accessibility-aware live indicator animation that can represent a real boolean state without inventing one, never animates unknown as live

"use client";

import React from "react";
import { getSemanticStatusLabel } from "@/lib/enterprise-ui/accessibility";

export type LivePulseState = "active" | "inactive" | "pending" | "unknown" | "error";

export interface LivePulseProps {
  readonly state: LivePulseState;
  readonly label?: string;
  readonly showLabel?: boolean;
  readonly size?: number;
  readonly className?: string;
  readonly style?: React.CSSProperties;
}

const stateConfig: Record<LivePulseState, { color: string; glow: string; animate: boolean; semantic: string }> = {
  active: {
    color: "var(--enterprise-success)",
    glow: "0 0 12px rgba(16, 185, 129, 0.6)",
    animate: true,
    semantic: "active",
  },
  inactive: {
    color: "var(--enterprise-text-tertiary)",
    glow: "none",
    animate: false,
    semantic: "inactive",
  },
  pending: {
    color: "var(--enterprise-warning)",
    glow: "0 0 12px rgba(245, 158, 11, 0.6)",
    animate: true,
    semantic: "pending",
  },
  error: {
    color: "var(--enterprise-danger)",
    glow: "0 0 12px rgba(239, 68, 68, 0.6)",
    animate: true,
    semantic: "error",
  },
  unknown: {
    color: "transparent",
    glow: "none",
    animate: false,
    semantic: "unknown",
  },
};

export function LivePulse({
  state,
  label,
  showLabel = true,
  size = 8,
  className,
  style,
}: LivePulseProps): React.ReactElement {
  const config = stateConfig[state] ?? stateConfig.unknown;
  const semanticLabel = label ?? getSemanticStatusLabel(config.semantic as unknown as Parameters<typeof getSemanticStatusLabel>[0]);

  // Never animate unknown as if it were live — enforced by config.animate = false for unknown
  const shouldAnimate = config.animate && state !== "unknown";

  return (
    <span
      className={["enterprise-live-pulse", className].filter(Boolean).join(" ")}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 8,
        ...style,
      }}
      role="status"
      aria-label={semanticLabel}
      aria-live={state === "error" || state === "active" ? "polite" : "off"}
    >
      <span
        aria-hidden="true"
        style={{
          position: "relative",
          width: size,
          height: size,
          borderRadius: "50%",
          background: config.color,
          border: state === "unknown" ? "1px dashed var(--enterprise-border-strong)" : "none",
          boxShadow: config.glow,
          flexShrink: 0,
          display: "inline-block",
        }}
      >
        {shouldAnimate && (
          <span
            aria-hidden="true"
            style={{
              position: "absolute",
              inset: -size / 2,
              borderRadius: "50%",
              background: config.color,
              opacity: 0.3,
              animation: "enterprise-pulse-ring 2s cubic-bezier(0.4, 0, 0.6, 1) infinite",
            }}
          />
        )}
      </span>

      {showLabel && (
        <span
          style={{
            fontSize: 11,
            fontWeight: 600,
            color: state === "unknown" ? "var(--enterprise-text-tertiary)" : "var(--enterprise-text-secondary)",
            letterSpacing: "0.02em",
          }}
        >
          {semanticLabel}
        </span>
      )}

      <style>{`
        @keyframes enterprise-pulse-ring {
          0% { transform: scale(0.8); opacity: 0.6; }
          50% { transform: scale(1.6); opacity: 0; }
          100% { transform: scale(1.6); opacity: 0; }
        }
        @media (prefers-reduced-motion: reduce) {
          @keyframes enterprise-pulse-ring {
            0% { transform: scale(1); opacity: 0.3; }
            100% { transform: scale(1); opacity: 0.3; }
          }
        }
      `}</style>
    </span>
  );
}
