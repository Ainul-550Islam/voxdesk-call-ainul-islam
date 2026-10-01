// File: components/enterprise/OrbitalSystem.tsx — Decorative orbital visualization component for system/AI-control-plane hero areas using deterministic geometry and no fake metrics

"use client";

import React, { useMemo } from "react";

export interface OrbitalNode {
  readonly id: string;
  readonly label?: string;
  readonly orbitIndex: number;
  readonly angleDeg: number;
  readonly size?: number;
  readonly color?: string;
}

export interface OrbitalSystemProps {
  readonly nodes?: readonly OrbitalNode[];
  readonly orbits?: number;
  readonly showCenter?: boolean;
  readonly centerLabel?: string;
  readonly size?: number;
  readonly className?: string;
  readonly style?: React.CSSProperties;
  readonly ariaLabel?: string;
}

const DEFAULT_ORBITS = 3;
const DEFAULT_SIZE = 320;

function getOrbitRadius(orbitIndex: number, totalSize: number, orbitCount: number): number {
  // Deterministic radii: 22%, 42%, 64% of half size, etc.
  const basePercent = 0.22 + orbitIndex * 0.22;
  const maxPercent = 0.88;
  const percent = Math.min(maxPercent, basePercent);
  return (totalSize / 2) * percent;
}

export function OrbitalSystem({
  nodes,
  orbits = DEFAULT_ORBITS,
  showCenter = true,
  centerLabel,
  size = DEFAULT_SIZE,
  className,
  style,
  ariaLabel = "System orbital visualization — decorative",
}: OrbitalSystemProps): React.ReactElement {
  const orbitCount = Math.max(1, Math.min(5, orbits));

  const deterministicNodes = useMemo(() => {
    if (nodes && nodes.length > 0) return nodes;
    // Decorative default nodes — deterministic, no fake telemetry
    const defaultNodes: OrbitalNode[] = [
      { id: "o-0-0", orbitIndex: 0, angleDeg: 30, label: "Core" },
      { id: "o-1-0", orbitIndex: 1, angleDeg: 120, label: "Agent" },
      { id: "o-1-1", orbitIndex: 1, angleDeg: 280, label: "Flow" },
      { id: "o-2-0", orbitIndex: 2, angleDeg: 60, label: "Edge" },
      { id: "o-2-1", orbitIndex: 2, angleDeg: 200, label: "Mesh" },
    ];
    return defaultNodes.filter((n) => n.orbitIndex < orbitCount);
  }, [nodes, orbitCount]);

  const center = size / 2;

  return (
    <div
      className={["enterprise-orbital", className].filter(Boolean).join(" ")}
      style={{
        width: size,
        height: size,
        position: "relative",
        display: "grid",
        placeItems: "center",
        ...style,
      }}
      role="img"
      aria-label={ariaLabel}
    >
      {/* Orbit rings */}
      {Array.from({ length: orbitCount }).map((_, i) => {
        const radius = getOrbitRadius(i, size, orbitCount);
        return (
          <div
            key={`orbit-${i}`}
            aria-hidden="true"
            style={{
              position: "absolute",
              left: center - radius,
              top: center - radius,
              width: radius * 2,
              height: radius * 2,
              borderRadius: "50%",
              border: `1px solid ${i === 0 ? "rgba(124,92,252,0.18)" : "rgba(255,255,255,0.08)"}`,
              boxShadow: i === 0 ? "0 0 20px rgba(124,92,252,0.12)" : "none",
            }}
          />
        );
      })}

      {/* Center */}
      {showCenter && (
        <div
          aria-hidden="true"
          style={{
            width: size * 0.12,
            height: size * 0.12,
            borderRadius: "50%",
            background: "linear-gradient(135deg, #7c5cfc 0%, #5b3fd8 100%)",
            boxShadow: "0 0 32px rgba(124,92,252,0.32), inset 0 1px 0 rgba(255,255,255,0.24)",
            position: "relative",
            zIndex: 2,
            display: "grid",
            placeItems: "center",
            color: "#fff",
            fontSize: 10,
            fontWeight: 800,
            letterSpacing: "0.04em",
          }}
          title={centerLabel ?? "System Core"}
        >
          {centerLabel ? centerLabel.slice(0, 2).toUpperCase() : "◉"}
        </div>
      )}

      {/* Nodes */}
      {deterministicNodes.map((node) => {
        const radius = getOrbitRadius(node.orbitIndex, size, orbitCount);
        const angleRad = (node.angleDeg * Math.PI) / 180;
        const x = center + radius * Math.cos(angleRad);
        const y = center + radius * Math.sin(angleRad);
        const nodeSize = node.size ?? (node.orbitIndex === 0 ? 10 : node.orbitIndex === 1 ? 8 : 7);

        return (
          <div
            key={node.id}
            aria-hidden="true"
            title={node.label ?? node.id}
            style={{
              position: "absolute",
              left: x - nodeSize / 2,
              top: y - nodeSize / 2,
              width: nodeSize,
              height: nodeSize,
              borderRadius: "50%",
              background: node.color ?? (node.orbitIndex === 0 ? "#a78bfa" : "rgba(255,255,255,0.82)"),
              boxShadow: `0 0 12px ${node.color ?? "rgba(255,255,255,0.4)"}`,
              zIndex: 1,
            }}
          />
        );
      })}

      {/* SVG connecting lines — decorative, no data */}
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        style={{ position: "absolute", inset: 0, pointerEvents: "none" }}
        aria-hidden="true"
      >
        {deterministicNodes.slice(0, 3).map((node) => {
          const radius = getOrbitRadius(node.orbitIndex, size, orbitCount);
          const angleRad = (node.angleDeg * Math.PI) / 180;
          const x = center + radius * Math.cos(angleRad);
          const y = center + radius * Math.sin(angleRad);
          return (
            <line
              key={`line-${node.id}`}
              x1={center}
              y1={center}
              x2={x}
              y2={y}
              stroke="rgba(124,92,252,0.10)"
              strokeWidth={1}
              strokeDasharray="3 4"
            />
          );
        })}
      </svg>
    </div>
  );
}
