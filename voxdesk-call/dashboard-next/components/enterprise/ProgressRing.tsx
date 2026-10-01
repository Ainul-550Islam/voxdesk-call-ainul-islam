// File: components/enterprise/ProgressRing.tsx — SVG progress-ring component supporting determinate percentage, indeterminate mode and accessible textual labels, no fake percentage

"use client";

import React from "react";

export interface ProgressRingProps {
  readonly value?: number | null;
  readonly min?: number;
  readonly max?: number;
  readonly size?: number;
  readonly strokeWidth?: number;
  readonly indeterminate?: boolean;
  readonly label?: string;
  readonly showValue?: boolean;
  readonly ariaLabel?: string;
  readonly className?: string;
  readonly style?: React.CSSProperties;
  readonly trackColor?: string;
  readonly progressColor?: string;
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

export function ProgressRing({
  value,
  min = 0,
  max = 100,
  size = 48,
  strokeWidth = 4,
  indeterminate = false,
  label,
  showValue = true,
  ariaLabel,
  className,
  style,
  trackColor = "rgba(255,255,255,0.08)",
  progressColor = "var(--enterprise-accent)",
}: ProgressRingProps): React.ReactElement {
  const hasValue = value !== null && value !== undefined && Number.isFinite(value) && !indeterminate;
  const clampedValue = hasValue ? clamp(value as number, min, max) : min;
  const range = max - min;
  const progress = range > 0 ? (clampedValue - min) / range : 0;

  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const strokeDashoffset = hasValue ? circumference * (1 - progress) : circumference * 0.25;

  const percentageText = hasValue ? `${Math.round(progress * 100)}%` : "—";
  const accessibleLabel = ariaLabel ?? label ?? (hasValue ? `Progress ${percentageText}` : "Progress unavailable");

  return (
    <div
      className={className}
      style={{
        width: size,
        height: size,
        position: "relative",
        display: "inline-grid",
        placeItems: "center",
        ...style,
      }}
      role="progressbar"
      aria-valuemin={hasValue ? min : undefined}
      aria-valuemax={hasValue ? max : undefined}
      aria-valuenow={hasValue ? clampedValue : undefined}
      aria-label={accessibleLabel}
      aria-valuetext={hasValue ? percentageText : "Unavailable"}
    >
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        style={{ transform: "rotate(-90deg)", display: "block" }}
        aria-hidden="true"
      >
        {/* Track */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={trackColor}
          strokeWidth={strokeWidth}
        />
        {/* Progress */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={progressColor}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={indeterminate ? circumference * 0.25 : strokeDashoffset}
          style={{
            transition: indeterminate ? "none" : "stroke-dashoffset 320ms cubic-bezier(0.16, 1, 0.3, 1)",
            transformOrigin: "center",
            animation: indeterminate ? "enterprise-progress-indeterminate 1.4s ease-in-out infinite" : "none",
          }}
        />
      </svg>

      {showValue && (
        <span
          style={{
            position: "absolute",
            fontSize: size <= 40 ? 10 : 12,
            fontWeight: 700,
            color: "var(--enterprise-text)",
            fontVariantNumeric: "tabular-nums",
            lineHeight: 1,
          }}
          aria-hidden="true"
        >
          {indeterminate ? "" : hasValue ? percentageText : "—"}
        </span>
      )}

      <style>{`
        @keyframes enterprise-progress-indeterminate {
          0% { transform: rotate(0deg); stroke-dasharray: ${circumference * 0.2} ${circumference * 0.8}; }
          50% { transform: rotate(180deg); stroke-dasharray: ${circumference * 0.6} ${circumference * 0.4}; }
          100% { transform: rotate(360deg); stroke-dasharray: ${circumference * 0.2} ${circumference * 0.8}; }
        }
        @media (prefers-reduced-motion: reduce) {
          @keyframes enterprise-progress-indeterminate {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(0deg); }
          }
        }
      `}</style>
    </div>
  );
}
