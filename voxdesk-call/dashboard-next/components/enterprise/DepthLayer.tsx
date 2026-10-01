// File: components/enterprise/DepthLayer.tsx — 3D depth wrapper controlling perspective, translateZ-style visual layering and safe hover depth with reduced-motion fallback

"use client";

import React, { forwardRef, useEffect, useState } from "react";
import { prefersReducedMotion, getSafeDuration } from "@/lib/enterprise-ui/motion";

export type DepthLevel = 0 | 1 | 2 | 3 | 4;

export interface DepthLayerProps extends React.HTMLAttributes<HTMLDivElement> {
  readonly depth?: DepthLevel;
  readonly perspective?: number;
  readonly hoverDepth?: boolean;
  readonly hoverTranslateZ?: number;
  readonly disabledOnReducedMotion?: boolean;
}

const depthTranslate: Record<DepthLevel, number> = {
  0: 0,
  1: 8,
  2: 16,
  3: 28,
  4: 44,
};

export const DepthLayer = forwardRef<HTMLDivElement, DepthLayerProps>(function DepthLayer(
  {
    depth = 1,
    perspective = 1200,
    hoverDepth = false,
    hoverTranslateZ = 12,
    disabledOnReducedMotion = true,
    className,
    style,
    children,
    onMouseEnter,
    onMouseLeave,
    ...rest
  },
  ref
) {
  const [isReducedMotion, setIsReducedMotion] = useState(false);
  const [isHovered, setIsHovered] = useState(false);

  useEffect(() => {
    setIsReducedMotion(prefersReducedMotion());
    if (!disabledOnReducedMotion) return;

    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const handler = () => setIsReducedMotion(mq.matches);
    mq.addEventListener?.("change", handler);
    return () => mq.removeEventListener?.("change", handler);
  }, [disabledOnReducedMotion]);

  const baseZ = depthTranslate[depth];
  const effectiveZ = hoverDepth && isHovered && !isReducedMotion ? baseZ + hoverTranslateZ : baseZ;
  const transitionDuration = isReducedMotion ? 0 : getSafeDuration("normal");

  const handleEnter = (e: React.MouseEvent<HTMLDivElement>) => {
    setIsHovered(true);
    onMouseEnter?.(e);
  };

  const handleLeave = (e: React.MouseEvent<HTMLDivElement>) => {
    setIsHovered(false);
    onMouseLeave?.(e);
  };

  return (
    <div
      style={{
        perspective: `${perspective}px`,
        perspectiveOrigin: "center center",
        transformStyle: "preserve-3d",
      }}
    >
      <div
        ref={ref}
        className={["enterprise-depth-layer", "enterprise-preserve-3d", className].filter(Boolean).join(" ")}
        data-depth={depth}
        style={{
          transform: isReducedMotion ? "none" : `translateZ(${effectiveZ}px)`,
          transition: isReducedMotion ? "none" : `transform ${transitionDuration}ms cubic-bezier(0.16, 1, 0.3, 1)`,
          transformStyle: "preserve-3d",
          willChange: isReducedMotion ? "auto" : "transform",
          ...style,
        }}
        onMouseEnter={handleEnter}
        onMouseLeave={handleLeave}
        {...rest}
      >
        {children}
      </div>
    </div>
  );
});

DepthLayer.displayName = "DepthLayer";
