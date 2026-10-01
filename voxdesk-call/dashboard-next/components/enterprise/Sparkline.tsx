// File: components/enterprise/Sparkline.tsx — Lightweight SVG sparkline component rendering supplied numeric series without inventing values, accessible label

"use client";

import React, { useMemo } from "react";

export interface SparklineProps {
  readonly values: readonly number[];
  readonly width?: number;
  readonly height?: number;
  readonly strokeWidth?: number;
  readonly strokeColor?: string;
  readonly fillColor?: string;
  readonly showArea?: boolean;
  readonly ariaLabel?: string;
  readonly className?: string;
  readonly style?: React.CSSProperties;
}

function normalizeValues(values: readonly number[]): { min: number; max: number; range: number } {
  if (values.length === 0) return { min: 0, max: 0, range: 0 };
  let min = values[0];
  let max = values[0];
  for (let i = 1; i < values.length; i++) {
    const v = values[i];
    if (!Number.isFinite(v)) continue;
    if (v < min) min = v;
    if (v > max) max = v;
  }
  const range = max - min;
  return { min, max, range };
}

export function Sparkline({
  values,
  width = 120,
  height = 32,
  strokeWidth = 1.5,
  strokeColor = "var(--enterprise-accent)",
  fillColor = "var(--enterprise-accent-glass)",
  showArea = true,
  ariaLabel,
  className,
  style,
}: SparklineProps): React.ReactElement {
  const validValues = useMemo(() => {
    return values.filter((v) => Number.isFinite(v));
  }, [values]);

  const { min, max, range } = useMemo(() => normalizeValues(validValues), [validValues]);

  const hasData = validValues.length >= 2 && range >= 0;

  const points = useMemo(() => {
    if (!hasData) return "";
    const stepX = validValues.length > 1 ? width / (validValues.length - 1) : width;
    const effectiveRange = range === 0 ? 1 : range;
    return validValues
      .map((v, i) => {
        const x = i * stepX;
        const normalizedY = (v - min) / effectiveRange; // 0..1
        const y = height - normalizedY * height; // invert Y
        // Clamp to viewBox
        const clampedY = Math.max(0, Math.min(height, y));
        return `${x.toFixed(2)},${clampedY.toFixed(2)}`;
      })
      .join(" ");
  }, [validValues, width, height, min, range, hasData]);

  const areaPath = useMemo(() => {
    if (!hasData || !showArea) return "";
    const stepX = validValues.length > 1 ? width / (validValues.length - 1) : width;
    const effectiveRange = range === 0 ? 1 : range;
    let d = "";
    validValues.forEach((v, i) => {
      const x = i * stepX;
      const normalizedY = (v - min) / effectiveRange;
      const y = height - normalizedY * height;
      const clampedY = Math.max(0, Math.min(height, y));
      if (i === 0) d += `M ${x.toFixed(2)} ${clampedY.toFixed(2)}`;
      else d += ` L ${x.toFixed(2)} ${clampedY.toFixed(2)}`;
    });
    // Close area to bottom
    d += ` L ${width.toFixed(2)} ${height.toFixed(2)} L 0 ${height.toFixed(2)} Z`;
    return d;
  }, [validValues, width, height, min, range, hasData, showArea]);

  if (!hasData) {
    return (
      <div
        className={className}
        style={{
          width,
          height,
          display: "grid",
          placeItems: "center",
          color: "var(--enterprise-text-tertiary)",
          fontSize: 10,
          ...style,
        }}
        aria-label={ariaLabel ?? "No sparkline data available"}
        role="img"
      >
        <span aria-hidden="true">—</span>
        <span className="enterprise-sr-only">{ariaLabel ?? "No sparkline data available"}</span>
      </div>
    );
  }

  const accessibleLabel =
    ariaLabel ?? `Sparkline with ${validValues.length} points, from ${min} to ${max}`;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
      style={{ display: "block", overflow: "visible", ...style }}
      role="img"
      aria-label={accessibleLabel}
      preserveAspectRatio="none"
    >
      {showArea && areaPath && (
        <path d={areaPath} fill={fillColor} stroke="none" opacity={0.6} />
      )}
      <polyline
        points={points}
        fill="none"
        stroke={strokeColor}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity={0.9}
      />
    </svg>
  );
}
