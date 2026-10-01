// File: components/enterprise/GlassPanel.tsx — Premium glass surface component with blur, translucency, border treatment, depth and configurable intensity

"use client";

import React, { forwardRef } from "react";
import { getShadow } from "@/lib/enterprise-ui/design-tokens";
import type { ElevationLevel } from "@/lib/enterprise-ui/design-tokens";

export type GlassPanelRadius = "sm" | "md" | "lg" | "xl" | "full";
export type GlassPanelElevation = ElevationLevel;
export type GlassPanelIntensity = "subtle" | "low" | "medium" | "high" | "strong";
export type GlassPanelBlur = "sm" | "md" | "lg" | "xl" | "glass";

export interface GlassPanelProps extends React.HTMLAttributes<HTMLDivElement> {
  readonly radius?: GlassPanelRadius;
  readonly elevation?: GlassPanelElevation;
  readonly intensity?: GlassPanelIntensity;
  readonly blur?: GlassPanelBlur;
  readonly hoverElevation?: boolean;
  readonly interactive?: boolean;
  readonly as?: "div" | "section" | "article" | "aside" | "header" | "footer" | "main";
}

const radiusMap: Record<GlassPanelRadius, string> = {
  sm: "var(--enterprise-radius-sm)",
  md: "var(--enterprise-radius-md)",
  lg: "var(--enterprise-radius-lg)",
  xl: "var(--enterprise-radius-xl)",
  full: "var(--enterprise-radius-full)",
};

const intensityMap: Record<GlassPanelIntensity, { bg: string; border: string }> = {
  subtle: {
    bg: "rgba(255,255,255,0.02)",
    border: "rgba(255,255,255,0.06)",
  },
  low: {
    bg: "rgba(255,255,255,0.04)",
    border: "rgba(255,255,255,0.08)",
  },
  medium: {
    bg: "rgba(255,255,255,0.06)",
    border: "rgba(255,255,255,0.10)",
  },
  high: {
    bg: "rgba(255,255,255,0.08)",
    border: "rgba(255,255,255,0.12)",
  },
  strong: {
    bg: "rgba(255,255,255,0.10)",
    border: "rgba(255,255,255,0.14)",
  },
};

const blurMap: Record<GlassPanelBlur, string> = {
  sm: "blur(12px)",
  md: "blur(20px)",
  lg: "blur(28px)",
  xl: "blur(36px) saturate(160%)",
  glass: "blur(24px) saturate(180%)",
};

export const GlassPanel = forwardRef<HTMLDivElement, GlassPanelProps>(function GlassPanel(
  {
    radius = "lg",
    elevation = 2,
    intensity = "medium",
    blur: blurProp = "glass",
    hoverElevation = false,
    interactive = false,
    as = "div",
    className,
    style,
    children,
    ...rest
  },
  ref
) {
  const intensityStyle = intensityMap[intensity];
  const blurValue = blurMap[blurProp];
  const shadow = getShadow(elevation);
  const hoverShadow = hoverElevation ? getShadow(Math.min(5, elevation + 1) as ElevationLevel) : shadow;

  const Component = as as unknown as React.ElementType;

  const combinedStyle: React.CSSProperties = {
    background: `linear-gradient(145deg, ${intensityStyle.bg}, rgba(255,255,255,0.01))`,
    backdropFilter: blurValue,
    WebkitBackdropFilter: blurValue,
    border: `1px solid ${intensityStyle.border}`,
    borderRadius: radiusMap[radius],
    boxShadow: shadow,
    position: "relative",
    overflow: "hidden",
    isolation: "isolate",
    ...style,
  };

  const combinedClassName = [
    "enterprise-glass",
    interactive || hoverElevation ? "enterprise-glass-interactive" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <Component
      ref={ref}
      className={combinedClassName}
      style={combinedStyle}
      data-elevation={elevation}
      data-intensity={intensity}
      // Store hover shadow for CSS to use if needed via data attribute
      data-hover-shadow={hoverElevation ? hoverShadow : undefined}
      {...rest}
    >
      {/* Edge highlight — subtle internal top border */}
      <span
        aria-hidden="true"
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          height: 1,
          background: "linear-gradient(90deg, transparent, rgba(255,255,255,0.18), transparent)",
          pointerEvents: "none",
          borderTopLeftRadius: radiusMap[radius],
          borderTopRightRadius: radiusMap[radius],
        }}
      />
      {children}
      {/* Fallback for backdrop-filter unsupported — solid bg via CSS @supports handled in enterprise.css */}
      <style>{`
        @supports not (backdrop-filter: blur(1px)) {
          .enterprise-glass {
            background: rgba(18, 18, 28, 0.92) !important;
          }
        }
      `}</style>
    </Component>
  );
});

GlassPanel.displayName = "GlassPanel";
