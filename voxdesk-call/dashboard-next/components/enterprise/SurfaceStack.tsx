// File: components/enterprise/SurfaceStack.tsx — Layered-surface layout component for composing foreground, midground and background glass surfaces with safe z-index and pointer behavior

"use client";

import React from "react";

export interface SurfaceStackProps extends React.HTMLAttributes<HTMLDivElement> {
  readonly back?: React.ReactNode;
  readonly mid?: React.ReactNode;
  readonly front?: React.ReactNode;
  readonly backClassName?: string;
  readonly midClassName?: string;
  readonly frontClassName?: string;
  readonly gap?: string;
}

export function SurfaceStack({
  back,
  mid,
  front,
  children,
  backClassName,
  midClassName,
  frontClassName,
  gap = "0px",
  className,
  style,
  ...rest
}: SurfaceStackProps): React.ReactElement {
  // If children provided, treat as front by default for flexibility
  const hasExplicitLayers = Boolean(back || mid || front);
  const effectiveFront = front ?? (hasExplicitLayers ? undefined : children);
  const effectiveChildren = hasExplicitLayers ? children : undefined;

  return (
    <div
      className={["enterprise-surface-stack", className].filter(Boolean).join(" ")}
      style={{
        position: "relative",
        isolation: "isolate",
        display: "flex",
        flexDirection: "column",
        gap,
        ...style,
      }}
      {...rest}
    >
      {back && (
        <div
          className={["enterprise-surface-stack__back", backClassName].filter(Boolean).join(" ")}
          style={{
            position: hasExplicitLayers ? "absolute" : "relative",
            inset: hasExplicitLayers ? 0 : undefined,
            zIndex: 0,
            pointerEvents: "none",
          }}
          aria-hidden="true"
        >
          {back}
        </div>
      )}

      {mid && (
        <div
          className={["enterprise-surface-stack__mid", midClassName].filter(Boolean).join(" ")}
          style={{
            position: "relative",
            zIndex: 1,
          }}
        >
          {mid}
        </div>
      )}

      {effectiveChildren && !hasExplicitLayers && (
        <div
          className={["enterprise-surface-stack__mid", midClassName].filter(Boolean).join(" ")}
          style={{ position: "relative", zIndex: 1 }}
        >
          {effectiveChildren}
        </div>
      )}

      {effectiveFront && (
        <div
          className={["enterprise-surface-stack__front", frontClassName].filter(Boolean).join(" ")}
          style={{
            position: "relative",
            zIndex: 2,
          }}
        >
          {effectiveFront}
        </div>
      )}

      {hasExplicitLayers && effectiveChildren && (
        <div style={{ position: "relative", zIndex: 1 }}>{effectiveChildren}</div>
      )}
    </div>
  );
}
