// File: components/enterprise/GlassBadge.tsx — Compact badge component supporting neutral, accent, success, warning and danger semantic variants with readable contrast

"use client";

import React from "react";

export type GlassBadgeVariant = "neutral" | "accent" | "success" | "warning" | "danger" | "info";
export type GlassBadgeSize = "sm" | "md";

export interface GlassBadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  readonly variant?: GlassBadgeVariant;
  readonly size?: GlassBadgeSize;
  readonly icon?: React.ReactNode;
  readonly dot?: boolean;
}

const variantStyles: Record<GlassBadgeVariant, { bg: string; color: string; border: string }> = {
  neutral: {
    bg: "rgba(255,255,255,0.06)",
    color: "var(--enterprise-text-secondary)",
    border: "rgba(255,255,255,0.10)",
  },
  accent: {
    bg: "rgba(124, 92, 252, 0.14)",
    color: "#a78bfa",
    border: "rgba(124, 92, 252, 0.24)",
  },
  success: {
    bg: "rgba(16, 185, 129, 0.14)",
    color: "#34d399",
    border: "rgba(16, 185, 129, 0.24)",
  },
  warning: {
    bg: "rgba(245, 158, 11, 0.14)",
    color: "#fbbf24",
    border: "rgba(245, 158, 11, 0.24)",
  },
  danger: {
    bg: "rgba(239, 68, 68, 0.14)",
    color: "#f87171",
    border: "rgba(239, 68, 68, 0.24)",
  },
  info: {
    bg: "rgba(59, 130, 246, 0.14)",
    color: "#60a5fa",
    border: "rgba(59, 130, 246, 0.24)",
  },
};

const sizeStyles: Record<GlassBadgeSize, { padding: string; fontSize: string; gap: string }> = {
  sm: { padding: "2px 8px", fontSize: "10px", gap: "4px" },
  md: { padding: "4px 10px", fontSize: "11px", gap: "6px" },
};

export function GlassBadge({
  variant = "neutral",
  size = "md",
  icon,
  dot = false,
  className,
  style,
  children,
  ...rest
}: GlassBadgeProps): React.ReactElement {
  const variantStyle = variantStyles[variant];
  const sizeStyle = sizeStyles[size];

  return (
    <span
      className={["enterprise-glass-badge", className].filter(Boolean).join(" ")}
      data-variant={variant}
      data-size={size}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: sizeStyle.gap,
        padding: sizeStyle.padding,
        borderRadius: "var(--enterprise-radius-full)",
        background: variantStyle.bg,
        color: variantStyle.color,
        border: `1px solid ${variantStyle.border}`,
        fontSize: sizeStyle.fontSize,
        fontWeight: 700,
        lineHeight: 1,
        letterSpacing: "0.02em",
        whiteSpace: "nowrap",
        backdropFilter: "blur(12px)",
        WebkitBackdropFilter: "blur(12px)",
        ...style,
      }}
      {...rest}
    >
      {dot && (
        <span
          aria-hidden="true"
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: "currentColor",
            flexShrink: 0,
            display: "inline-block",
          }}
        />
      )}
      {icon && (
        <span aria-hidden="true" style={{ display: "inline-flex", alignItems: "center", fontSize: size === "sm" ? 10 : 12 }}>
          {icon}
        </span>
      )}
      <span>{children}</span>
    </span>
  );
}
