// File: components/enterprise/MetricCard3D.tsx — 3D enterprise metric card supporting label, value, delta, icon, trend state and optional sparkline slot using real typed props, no fake data

"use client";

import React from "react";
import { GlassCard } from "./GlassCard";
import { TiltSurface } from "./TiltSurface";
import { DepthLayer } from "./DepthLayer";
import { formatMetric, formatDelta, isUnavailable, UNAVAILABLE_SYMBOL } from "@/lib/enterprise-ui/visual-format";
import { getMetricAriaLabel, getSemanticStatusLabel } from "@/lib/enterprise-ui/accessibility";
import type { SemanticStatus } from "@/lib/enterprise-ui/accessibility";

export type MetricTrend = "up" | "down" | "neutral" | "unknown";
export type MetricStatus = SemanticStatus;

export interface MetricCard3DProps {
  readonly label: string;
  readonly value?: number | string | null;
  readonly unit?: string;
  readonly delta?: number | null;
  readonly deltaLabel?: string;
  readonly trend?: MetricTrend;
  readonly status?: MetricStatus;
  readonly icon?: React.ReactNode;
  readonly sparkline?: React.ReactNode;
  readonly description?: string;
  readonly isLoading?: boolean;
  readonly disabled?: boolean;
  readonly id?: string;
  readonly className?: string;
  readonly style?: React.CSSProperties;
  readonly onClick?: () => void;
}

function getTrendFromDelta(delta: number | null | undefined, explicitTrend?: MetricTrend): MetricTrend {
  if (explicitTrend) return explicitTrend;
  if (delta === null || delta === undefined) return "unknown";
  if (!Number.isFinite(delta)) return "unknown";
  if (delta > 0) return "up";
  if (delta < 0) return "down";
  return "neutral";
}

function getDeltaAriaDirection(trend: MetricTrend): string {
  switch (trend) {
    case "up":
      return "increased";
    case "down":
      return "decreased";
    case "neutral":
      return "no change";
    default:
      return "unknown change";
  }
}

export function MetricCard3D({
  label,
  value,
  unit,
  delta,
  deltaLabel,
  trend,
  status = "unknown",
  icon,
  sparkline,
  description,
  isLoading = false,
  disabled = false,
  id,
  className,
  style,
  onClick,
}: MetricCard3DProps): React.ReactElement {
  const effectiveTrend = getTrendFromDelta(delta, trend);
  const formattedValue = formatMetric(value as number | string | null | undefined);
  const isValueUnavailable = isUnavailable(value) || formattedValue === UNAVAILABLE_SYMBOL;
  const deltaResult = delta !== null && delta !== undefined ? formatDelta(delta) : null;

  const ariaLabel = getMetricAriaLabel({
    label,
    value: isValueUnavailable ? null : formattedValue,
    unit,
    delta: deltaResult && deltaResult.text !== UNAVAILABLE_SYMBOL ? deltaResult.text : undefined,
    status,
  });

  const statusLabel = getSemanticStatusLabel(status);

  return (
    <TiltSurface maxRotationDeg={6} scaleOnHover={1.02} disabled={disabled || isLoading}>
      <DepthLayer depth={1} hoverDepth>
        <GlassCard
          id={id}
          density="comfortable"
          hoverDepth={!disabled && !isLoading}
          elevation={2}
          intensity="medium"
          className={["enterprise-surface-hover", className].filter(Boolean).join(" ")}
          style={{
            minHeight: 132,
            opacity: disabled ? 0.6 : 1,
            pointerEvents: disabled ? "none" : "auto",
            cursor: onClick ? "pointer" : "default",
            ...style,
          }}
          aria-label={ariaLabel}
          role={onClick ? "button" : "group"}
          tabIndex={onClick && !disabled ? 0 : undefined}
          onClick={onClick}
          onKeyDown={(e) => {
            if (!onClick || disabled) return;
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              onClick();
            }
          }}
        >
          {/* Header: label + icon + status */}
          <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0, flex: 1 }}>
              <span className="enterprise-metric-label" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                {label}
                {status !== "unknown" && (
                  <span
                    aria-label={`Status: ${statusLabel}`}
                    title={statusLabel}
                    style={{
                      width: 6,
                      height: 6,
                      borderRadius: "50%",
                      background:
                        status === "healthy" || status === "active"
                          ? "var(--enterprise-success)"
                          : status === "warning"
                            ? "var(--enterprise-warning)"
                            : status === "error" || status === "degraded"
                              ? "var(--enterprise-danger)"
                              : status === "pending"
                                ? "var(--enterprise-warning)"
                                : "var(--enterprise-text-tertiary)",
                      display: "inline-block",
                      flexShrink: 0,
                    }}
                  />
                )}
              </span>
              {description && (
                <span
                  style={{
                    fontSize: 11,
                    color: "var(--enterprise-text-tertiary)",
                    lineHeight: 1.4,
                    display: "-webkit-box",
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: "vertical",
                    overflow: "hidden",
                  }}
                >
                  {description}
                </span>
              )}
            </div>
            {icon && (
              <span
                aria-hidden="true"
                style={{
                  width: 32,
                  height: 32,
                  borderRadius: "var(--enterprise-radius-sm)",
                  background: "var(--enterprise-surface-glass)",
                  border: "1px solid var(--enterprise-border)",
                  display: "grid",
                  placeItems: "center",
                  flexShrink: 0,
                  fontSize: 14,
                }}
              >
                {icon}
              </span>
            )}
          </div>

          {/* Value */}
          <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap", marginTop: 4 }}>
            {isLoading ? (
              <span
                aria-label="Loading metric value"
                style={{
                  width: 88,
                  height: 28,
                  borderRadius: 6,
                  background: "linear-gradient(90deg, rgba(255,255,255,0.06) 25%, rgba(255,255,255,0.10) 50%, rgba(255,255,255,0.06) 75%)",
                  backgroundSize: "200% 100%",
                  animation: "enterprise-shimmer 1.2s ease-in-out infinite",
                  display: "inline-block",
                }}
              />
            ) : (
              <>
                <span
                  className="enterprise-metric-value"
                  style={{ fontSize: 28, lineHeight: 1, letterSpacing: "-0.03em" }}
                  aria-label={isValueUnavailable ? "Value unavailable" : `Value ${formattedValue}${unit ? ` ${unit}` : ""}`}
                >
                  {formattedValue}
                </span>
                {unit && !isValueUnavailable && (
                  <span style={{ fontSize: 12, color: "var(--enterprise-text-secondary)", fontWeight: 500 }}>{unit}</span>
                )}
              </>
            )}

            {deltaResult && deltaResult.text !== UNAVAILABLE_SYMBOL && !isLoading && (
              <span
                className={`enterprise-metric-delta enterprise-metric-delta--${effectiveTrend === "up" ? "up" : effectiveTrend === "down" ? "down" : "neutral"}`}
                aria-label={`${getDeltaAriaDirection(effectiveTrend)} ${deltaResult.text}${deltaLabel ? ` ${deltaLabel}` : ""}`}
                title={deltaLabel ?? deltaResult.text}
              >
                <span aria-hidden="true">{effectiveTrend === "up" ? "↑" : effectiveTrend === "down" ? "↓" : "→"}</span>
                {deltaResult.text}
              </span>
            )}
          </div>

          {/* Sparkline slot — caller provides real series, no fake data */}
          {sparkline && (
            <div style={{ marginTop: 8, height: 32, opacity: disabled ? 0.5 : 1 }} aria-hidden="true">
              {sparkline}
            </div>
          )}

          {/* Accessibility: hidden description for screen readers when value unavailable */}
          {isValueUnavailable && !isLoading && (
            <span className="enterprise-sr-only">Metric {label} is unavailable. No fallback value is fabricated.</span>
          )}

          <style>{`
            @keyframes enterprise-shimmer {
              0% { background-position: -200% 0; }
              100% { background-position: 200% 0; }
            }
          `}</style>
        </GlassCard>
      </DepthLayer>
    </TiltSurface>
  );
}
