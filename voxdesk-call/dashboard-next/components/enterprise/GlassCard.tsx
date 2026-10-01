// File: components/enterprise/GlassCard.tsx — Reusable elevated glass card for enterprise content and metric surfaces with title, body, footer slots and hover depth

"use client";

import React, { forwardRef } from "react";
import { GlassPanel, type GlassPanelProps } from "./GlassPanel";

export type GlassCardDensity = "compact" | "comfortable" | "spacious";

export interface GlassCardProps extends Omit<GlassPanelProps, "as" | "title"> {
  readonly title?: React.ReactNode;
  readonly titleId?: string;
  readonly description?: React.ReactNode;
  readonly footer?: React.ReactNode;
  readonly density?: GlassCardDensity;
  readonly hoverDepth?: boolean;
  readonly as?: "div" | "section" | "article";
}

const densityPadding: Record<GlassCardDensity, string> = {
  compact: "16px",
  comfortable: "24px",
  spacious: "32px",
};

export const GlassCard = forwardRef<HTMLDivElement, GlassCardProps>(function GlassCard(
  {
    title,
    titleId,
    description,
    footer,
    density = "comfortable",
    hoverDepth = true,
    radius = "lg",
    elevation = 2,
    intensity = "medium",
    className,
    style,
    children,
    as = "section",
    ...rest
  },
  ref
) {
  const padding = densityPadding[density];
  const generatedTitleId = React.useId();
  const effectiveTitleId = titleId ?? (title ? `glass-card-title-${generatedTitleId}` : undefined);

  return (
    <GlassPanel
      ref={ref}
      as={as}
      radius={radius}
      elevation={elevation}
      intensity={intensity}
      hoverElevation={hoverDepth}
      interactive={hoverDepth}
      className={["enterprise-surface-highlight", className].filter(Boolean).join(" ")}
      style={{
        display: "flex",
        flexDirection: "column",
        ...style,
      }}
      aria-labelledby={effectiveTitleId}
      {...rest}
    >
      {(title || description) && (
        <header
          style={{
            padding: `${padding} ${padding} 0 ${padding}`,
            display: "flex",
            flexDirection: "column",
            gap: 6,
          }}
        >
          {title && (
            <h3
              id={effectiveTitleId}
              style={{
                margin: 0,
                fontSize: 14,
                fontWeight: 700,
                letterSpacing: "-0.01em",
                color: "var(--enterprise-text)",
                lineHeight: 1.3,
              }}
            >
              {title}
            </h3>
          )}
          {description && (
            <p
              style={{
                margin: 0,
                fontSize: 12,
                lineHeight: 1.5,
                color: "var(--enterprise-text-secondary)",
              }}
            >
              {description}
            </p>
          )}
        </header>
      )}

      <div
        style={{
          padding: title || description ? `${density === "compact" ? "12px" : "16px"} ${padding} ${footer ? (density === "compact" ? "12px" : "16px") : padding} ${padding}` : padding,
          flex: 1,
          display: "flex",
          flexDirection: "column",
          gap: density === "compact" ? 8 : 12,
        }}
      >
        {children}
      </div>

      {footer && (
        <footer
          style={{
            padding: `0 ${padding} ${padding} ${padding}`,
            borderTop: "1px solid var(--enterprise-border)",
            marginTop: "auto",
            paddingTop: density === "compact" ? 12 : 16,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 12,
          }}
        >
          {footer}
        </footer>
      )}
    </GlassPanel>
  );
});

GlassCard.displayName = "GlassCard";
