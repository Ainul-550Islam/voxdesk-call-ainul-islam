// File: components/enterprise/AmbientGlow.tsx — Decorative non-informational radial/ambient glow layer for premium depth without conveying state, no fake metrics

"use client";

import React from "react";

export type AmbientGlowVariant = "accent" | "success" | "warning" | "info" | "neutral" | "mesh";
export type AmbientGlowSize = "sm" | "md" | "lg" | "xl" | "full";

export interface AmbientGlowProps extends React.HTMLAttributes<HTMLDivElement> {
  readonly variant?: AmbientGlowVariant;
  readonly size?: AmbientGlowSize;
  readonly intensity?: number;
  readonly blur?: number;
  readonly x?: string;
  readonly y?: string;
}

const sizeMap: Record<AmbientGlowSize, { width: string; height: string }> = {
  sm: { width: "240px", height: "240px" },
  md: { width: "400px", height: "400px" },
  lg: { width: "640px", height: "640px" },
  xl: { width: "960px", height: "960px" },
  full: { width: "100%", height: "100%" },
};

const variantMap: Record<AmbientGlowVariant, string> = {
  accent: "radial-gradient(circle, rgba(124, 92, 252, 0.22) 0%, rgba(124, 92, 252, 0.06) 36%, transparent 72%)",
  success: "radial-gradient(circle, rgba(16, 185, 129, 0.18) 0%, rgba(16, 185, 129, 0.04) 36%, transparent 72%)",
  warning: "radial-gradient(circle, rgba(245, 158, 11, 0.16) 0%, rgba(245, 158, 11, 0.04) 36%, transparent 72%)",
  info: "radial-gradient(circle, rgba(59, 130, 246, 0.18) 0%, rgba(59, 130, 246, 0.04) 36%, transparent 72%)",
  neutral: "radial-gradient(circle, rgba(255,255,255,0.08) 0%, rgba(255,255,255,0.02) 36%, transparent 72%)",
  mesh: "radial-gradient(at 30% 20%, rgba(124,92,252,0.20) 0%, transparent 50%), radial-gradient(at 80% 60%, rgba(59,130,246,0.16) 0%, transparent 50%)",
};

export function AmbientGlow({
  variant = "accent",
  size = "lg",
  intensity = 1,
  blur = 48,
  x = "50%",
  y = "50%",
  className,
  style,
  ...rest
}: AmbientGlowProps): React.ReactElement {
  const sizeConfig = sizeMap[size];
  const background = variantMap[variant];

  // Clamp intensity 0..1, blur 0..120
  const clampedIntensity = Math.max(0, Math.min(1, intensity));
  const clampedBlur = Math.max(0, Math.min(120, blur));

  return (
    <div
      aria-hidden="true"
      data-variant={variant}
      data-size={size}
      className={["enterprise-ambient-glow", className].filter(Boolean).join(" ")}
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: sizeConfig.width,
        height: sizeConfig.height,
        transform: "translate(-50%, -50%)",
        background,
        filter: `blur(${clampedBlur}px)`,
        opacity: clampedIntensity * 0.9,
        pointerEvents: "none",
        borderRadius: "50%",
        mixBlendMode: "screen",
        willChange: "filter",
        ...style,
      }}
      {...rest}
    />
  );
}
