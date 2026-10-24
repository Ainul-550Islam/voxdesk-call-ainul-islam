"use client";

import React from "react";
import type { UiConnectionStatus, VerificationTier } from "../lib/api";

export interface StatusBadgeProps {
  status: UiConnectionStatus | VerificationTier | string;
}

const LABELS_AND_STYLES: Record<
  string,
  { label: string; bg: string; fg: string; border: string }
> = {
  CONFIGURED: {
    label: "Configured",
    bg: "#dcfce7",
    fg: "#166534",
    border: "#86efac",
  },
  NOT_CONFIGURED: {
    label: "Not configured",
    bg: "#f1f5f9",
    fg: "#475569",
    border: "#cbd5e1",
  },
  ERROR: {
    label: "Error",
    bg: "#fee2e2",
    fg: "#991b1b",
    border: "#fca5a5",
  },
  LIVE_VERIFIED: {
    label: "LIVE_VERIFIED",
    bg: "#dcfce7",
    fg: "#166534",
    border: "#86efac",
  },
  PROTOCOL_TESTED: {
    label: "PROTOCOL_TESTED",
    bg: "#e0f2fe",
    fg: "#075985",
    border: "#7dd3fc",
  },
};

export function StatusBadge({ status }: StatusBadgeProps) {
  const normalized = (status || "NOT_CONFIGURED").toUpperCase();
  const entry =
    LABELS_AND_STYLES[normalized] ?? LABELS_AND_STYLES.NOT_CONFIGURED;
  return (
    <span
      data-testid={`status-badge-${normalized}`}
      style={{
        display: "inline-block",
        fontSize: "0.75rem",
        fontWeight: 600,
        padding: "0.18rem 0.55rem",
        borderRadius: 999,
        backgroundColor: entry.bg,
        color: entry.fg,
        border: `1px solid ${entry.border}`,
      }}
    >
      {entry.label}
    </span>
  );
}
