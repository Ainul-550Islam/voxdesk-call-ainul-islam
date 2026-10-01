// File: components/enterprise/NeuralGrid.tsx — Subtle AI-style background grid with configurable density and safe performance defaults, low rendering cost

"use client";

import React from "react";

export type NeuralGridDensity = "sparse" | "normal" | "dense" | "ultra-dense";
export type NeuralGridVariant = "grid" | "dots" | "lines";

export interface NeuralGridProps extends React.HTMLAttributes<HTMLDivElement> {
  readonly density?: NeuralGridDensity;
  readonly variant?: NeuralGridVariant;
  readonly fade?: boolean;
  readonly opacity?: number;
  readonly color?: string;
}

const densitySize: Record<NeuralGridDensity, number> = {
  sparse: 48,
  normal: 32,
  dense: 24,
  "ultra-dense": 16,
};

export function NeuralGrid({
  density = "normal",
  variant = "grid",
  fade = true,
  opacity = 0.5,
  color = "rgba(255,255,255,0.04)",
  className,
  style,
  ...rest
}: NeuralGridProps): React.ReactElement {
  const gridSize = densitySize[density];
  const clampedOpacity = Math.max(0, Math.min(1, opacity));

  const backgroundImage =
    variant === "dots"
      ? `radial-gradient(${color} 1px, transparent 1px)`
      : variant === "lines"
        ? `linear-gradient(${color} 1px, transparent 1px)`
        : `linear-gradient(${color} 1px, transparent 1px), linear-gradient(90deg, ${color} 1px, transparent 1px)`;

  return (
    <div
      aria-hidden="true"
      data-density={density}
      data-variant={variant}
      className={["enterprise-neural-grid", className].filter(Boolean).join(" ")}
      style={{
        position: "absolute",
        inset: 0,
        backgroundImage,
        backgroundSize: variant === "lines" ? `100% ${gridSize}px` : `${gridSize}px ${gridSize}px`,
        maskImage: fade ? "radial-gradient(ellipse at center, black 36%, transparent 82%)" : undefined,
        WebkitMaskImage: fade ? "radial-gradient(ellipse at center, black 36%, transparent 82%)" : undefined,
        opacity: clampedOpacity,
        pointerEvents: "none",
        willChange: "auto",
        ...style,
      }}
      {...rest}
    />
  );
}
