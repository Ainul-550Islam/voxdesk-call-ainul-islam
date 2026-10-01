// File: components/enterprise/GlassDivider.tsx — Subtle glass separator component for dense enterprise panels and sections with optional label

"use client";

import React from "react";

export type GlassDividerOrientation = "horizontal" | "vertical";
export type GlassDividerVariant = "subtle" | "strong" | "glow";

export interface GlassDividerProps extends React.HTMLAttributes<HTMLHRElement> {
  readonly orientation?: GlassDividerOrientation;
  readonly variant?: GlassDividerVariant;
  readonly label?: string;
  readonly labelPosition?: "start" | "center" | "end";
}

const variantStyles: Record<GlassDividerVariant, { bg: string; shadow?: string }> = {
  subtle: {
    bg: "linear-gradient(90deg, transparent, rgba(255,255,255,0.10), transparent)",
  },
  strong: {
    bg: "linear-gradient(90deg, transparent, rgba(255,255,255,0.18), transparent)",
  },
  glow: {
    bg: "linear-gradient(90deg, transparent, rgba(124,92,252,0.24), transparent)",
    shadow: "0 0 12px rgba(124,92,252,0.24)",
  },
};

export function GlassDivider({
  orientation = "horizontal",
  variant = "subtle",
  label,
  labelPosition = "center",
  className,
  style,
  ...rest
}: GlassDividerProps): React.ReactElement {
  const variantStyle = variantStyles[variant];

  if (orientation === "vertical") {
    return (
      <div
        role="separator"
        aria-orientation="vertical"
        className={["enterprise-divider", "enterprise-divider--vertical", className].filter(Boolean).join(" ")}
        style={{
          width: 1,
          alignSelf: "stretch",
          background: variant === "glow" ? "linear-gradient(180deg, transparent, rgba(124,92,252,0.24), transparent)" : "linear-gradient(180deg, transparent, rgba(255,255,255,0.10), transparent)",
          boxShadow: variantStyle.shadow,
          flexShrink: 0,
          ...style,
        }}
        {...(rest as React.HTMLAttributes<HTMLDivElement>)}
      />
    );
  }

  if (label) {
    const justify =
      labelPosition === "start" ? "flex-start" : labelPosition === "end" ? "flex-end" : "center";

    return (
      <div
        role="separator"
        className={["enterprise-divider", "enterprise-divider--with-label", className].filter(Boolean).join(" ")}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 16,
          justifyContent: justify as unknown as string,
          width: "100%",
          ...style,
        }}
        {...(rest as React.HTMLAttributes<HTMLDivElement>)}
      >
        <span
          aria-hidden="true"
          style={{
            flex: labelPosition === "center" ? 1 : labelPosition === "start" ? 0 : 1,
            minWidth: labelPosition !== "center" ? 24 : undefined,
            height: 1,
            background: variantStyle.bg,
            boxShadow: variantStyle.shadow,
          }}
        />
        <span
          style={{
            fontSize: 11,
            fontWeight: 600,
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            color: "var(--enterprise-text-tertiary)",
            whiteSpace: "nowrap",
            padding: "2px 10px",
            borderRadius: "var(--enterprise-radius-full)",
            background: "var(--enterprise-surface-glass)",
            border: "1px solid var(--enterprise-border)",
            backdropFilter: "blur(12px)",
          }}
        >
          {label}
        </span>
        <span
          aria-hidden="true"
          style={{
            flex: labelPosition === "center" ? 1 : labelPosition === "end" ? 0 : 1,
            minWidth: labelPosition !== "center" ? 24 : undefined,
            height: 1,
            background: variantStyle.bg,
            boxShadow: variantStyle.shadow,
          }}
        />
      </div>
    );
  }

  return (
    <hr
      className={["enterprise-divider", className].filter(Boolean).join(" ")}
      style={{
        height: 1,
        border: "none",
        background: variantStyle.bg,
        boxShadow: variantStyle.shadow,
        margin: 0,
        ...style,
      }}
      {...rest}
    />
  );
}
