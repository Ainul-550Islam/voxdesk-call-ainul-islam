// File: components/enterprise/MetricGrid.tsx — Responsive metric-grid layout managing density, card sizing and breakpoint behavior for enterprise metrics

"use client";

import React from "react";

export type MetricGridDensity = "compact" | "comfortable" | "dense" | "spacious";

export interface MetricGridProps extends React.HTMLAttributes<HTMLDivElement> {
  readonly minCardWidth?: number;
  readonly gap?: string;
  readonly density?: MetricGridDensity;
  readonly columns?: number;
}

const densityGap: Record<MetricGridDensity, string> = {
  compact: "12px",
  comfortable: "16px",
  dense: "10px",
  spacious: "24px",
};

const densityMinWidth: Record<MetricGridDensity, number> = {
  compact: 180,
  comfortable: 220,
  dense: 160,
  spacious: 280,
};

export function MetricGrid({
  minCardWidth,
  gap,
  density = "comfortable",
  columns,
  className,
  style,
  children,
  ...rest
}: MetricGridProps): React.ReactElement {
  const effectiveMinWidth = minCardWidth ?? densityMinWidth[density];
  const effectiveGap = gap ?? densityGap[density];

  const gridTemplateColumns = columns
    ? `repeat(${columns}, minmax(0, 1fr))`
    : `repeat(auto-fit, minmax(${effectiveMinWidth}px, 1fr))`;

  return (
    <div
      className={["enterprise-metric-grid", className].filter(Boolean).join(" ")}
      style={{
        display: "grid",
        gridTemplateColumns,
        gap: effectiveGap,
        alignItems: "stretch",
        ...style,
      }}
      role="list"
      aria-label="Metrics"
      {...rest}
    >
      {React.Children.map(children, (child, index) => {
        if (!React.isValidElement(child)) return child;
        // Wrap each child in listitem for semantics, but preserve original
        return (
          <div role="listitem" style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
            {child}
          </div>
        );
      })}
      <style>{`
        .enterprise-metric-grid {
          width: 100%;
        }
        @media (max-width: 640px) {
          .enterprise-metric-grid {
            grid-template-columns: 1fr !important;
          }
        }
        @media (min-width: 641px) and (max-width: 1024px) {
          .enterprise-metric-grid {
            grid-template-columns: repeat(auto-fit, minmax(${Math.max(160, effectiveMinWidth - 20)}px, 1fr)) !important;
          }
        }
      `}</style>
    </div>
  );
}
