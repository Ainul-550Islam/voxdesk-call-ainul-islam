// File: components/enterprise/DataState.tsx — Shared data-state presentation for loading, empty, error and unavailable conditions without fabricated fallback values

"use client";

import React from "react";
import { GlassButton } from "./GlassButton";

export type DataStateVariant = "loading" | "empty" | "error" | "unavailable" | "forbidden";

export interface DataStateProps {
  readonly variant: DataStateVariant;
  readonly title?: string;
  readonly description?: string;
  readonly icon?: React.ReactNode;
  readonly actionLabel?: string;
  readonly onAction?: () => void;
  readonly onRetry?: () => void;
  readonly retryLabel?: string;
  readonly className?: string;
  readonly style?: React.CSSProperties;
  readonly children?: React.ReactNode;
}

const variantDefaults: Record<DataStateVariant, { title: string; description: string; icon: string }> = {
  loading: {
    title: "Loading",
    description: "Fetching data from the control plane. Please wait.",
    icon: "◍",
  },
  empty: {
    title: "No data",
    description: "There is no data to display for this view yet.",
    icon: "∅",
  },
  error: {
    title: "Something went wrong",
    description: "We encountered an error while loading this data. You can try again.",
    icon: "⚠",
  },
  unavailable: {
    title: "Unavailable",
    description: "This data is currently unavailable. It may be disabled, not configured, or awaiting activation.",
    icon: "—",
  },
  forbidden: {
    title: "Access restricted",
    description: "You do not have permission to view this data. Contact your administrator if you need access.",
    icon: "🔒",
  },
};

export function DataState({
  variant,
  title,
  description,
  icon,
  actionLabel,
  onAction,
  onRetry,
  retryLabel = "Retry",
  className,
  style,
  children,
}: DataStateProps): React.ReactElement {
  const defaults = variantDefaults[variant];
  const effectiveTitle = title ?? defaults.title;
  const effectiveDescription = description ?? defaults.description;
  const effectiveIcon = icon ?? defaults.icon;

  const isLoading = variant === "loading";

  return (
    <div
      className={["enterprise-data-state", className].filter(Boolean).join(" ")}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 16,
        padding: "48px 24px",
        textAlign: "center",
        minHeight: 240,
        ...style,
      }}
      role="status"
      aria-live={variant === "error" ? "assertive" : "polite"}
      aria-label={`${effectiveTitle}: ${effectiveDescription}`}
    >
      <div
        className="enterprise-data-state__icon"
        aria-hidden="true"
        style={{
          width: 48,
          height: 48,
          borderRadius: "var(--enterprise-radius-md)",
          background: variant === "error" ? "var(--enterprise-danger-glass)" : variant === "forbidden" ? "rgba(255,255,255,0.04)" : "var(--enterprise-surface-glass)",
          border: `1px solid ${variant === "error" ? "rgba(239,68,68,0.24)" : "var(--enterprise-border)"}`,
          display: "grid",
          placeItems: "center",
          fontSize: 20,
          color: variant === "error" ? "var(--enterprise-danger)" : variant === "forbidden" ? "var(--enterprise-text-tertiary)" : "var(--enterprise-text-secondary)",
        }}
      >
        {isLoading ? (
          <span
            style={{
              width: 20,
              height: 20,
              border: "2px solid currentColor",
              borderTopColor: "transparent",
              borderRadius: "50%",
              display: "inline-block",
              animation: "enterprise-spin 0.8s linear infinite",
            }}
          />
        ) : (
          effectiveIcon
        )}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 6, alignItems: "center" }}>
        <h3
          className="enterprise-data-state__title"
          style={{
            margin: 0,
            fontSize: 15,
            fontWeight: 600,
            color: "var(--enterprise-text)",
            lineHeight: 1.3,
          }}
        >
          {effectiveTitle}
        </h3>
        <p
          className="enterprise-data-state__description"
          style={{
            margin: 0,
            fontSize: 13,
            color: "var(--enterprise-text-secondary)",
            maxWidth: 360,
            lineHeight: 1.5,
          }}
        >
          {effectiveDescription}
        </p>
      </div>

      {children && <div style={{ marginTop: 4 }}>{children}</div>}

      {(onRetry || onAction) && (
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 4 }}>
          {onRetry && (
            <GlassButton
              variant="secondary"
              size="sm"
              onClick={onRetry}
              aria-label={retryLabel}
              disabled={isLoading}
            >
              {retryLabel}
            </GlassButton>
          )}
          {onAction && actionLabel && (
            <GlassButton variant={variant === "error" ? "secondary" : "primary"} size="sm" onClick={onAction}>
              {actionLabel}
            </GlassButton>
          )}
        </div>
      )}

      <style>{`
        @keyframes enterprise-spin {
          to { transform: rotate(360deg); }
        }
        @media (prefers-reduced-motion: reduce) {
          .enterprise-data-state__icon span {
            animation: none !important;
          }
        }
      `}</style>
    </div>
  );
}
