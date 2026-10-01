// File: components/enterprise/EnterpriseShell.tsx — New reusable enterprise shell component providing global visual frame, header area, content region and responsive composition without replacing the existing dashboard shell

"use client";

import React from "react";
import { AmbientGlow } from "./AmbientGlow";
import { NeuralGrid } from "./NeuralGrid";

export interface EnterpriseShellProps {
  readonly children: React.ReactNode;
  readonly topChrome?: React.ReactNode;
  readonly commandSurface?: React.ReactNode;
  readonly sidebar?: React.ReactNode;
  readonly backgroundLayers?: React.ReactNode;
  readonly isLoading?: boolean;
  readonly contentPadding?: string;
  readonly maxWidth?: string;
  readonly className?: string;
  readonly style?: React.CSSProperties;
  readonly id?: string;
}

export function EnterpriseShell({
  children,
  topChrome,
  commandSurface,
  sidebar,
  backgroundLayers,
  isLoading = false,
  contentPadding = "24px",
  maxWidth = "1440px",
  className,
  style,
  id,
}: EnterpriseShellProps): React.ReactElement {
  return (
    <div
      id={id}
      className={["enterprise-root", "enterprise-shell", className].filter(Boolean).join(" ")}
      style={{
        minHeight: "100vh",
        position: "relative",
        isolation: "isolate",
        background: "var(--enterprise-bg)",
        color: "var(--enterprise-text)",
        display: "flex",
        flexDirection: "column",
        ...style,
      }}
    >
      {/* Background layers — decorative only, no state */}
      <div
        aria-hidden="true"
        style={{
          position: "fixed",
          inset: 0,
          zIndex: -2,
          pointerEvents: "none",
          overflow: "hidden",
        }}
      >
        {backgroundLayers ?? (
          <>
            <div
              style={{
                position: "absolute",
                inset: 0,
                background: "var(--enterprise-gradient-mesh)",
                opacity: 0.9,
              }}
            />
            <NeuralGrid density="normal" opacity={0.4} fade />
            <AmbientGlow variant="accent" size="xl" x="18%" y="22%" intensity={0.6} blur={64} />
            <AmbientGlow variant="info" size="lg" x="82%" y="18%" intensity={0.4} blur={56} />
            <AmbientGlow variant="neutral" size="xl" x="50%" y="88%" intensity={0.3} blur={72} />
          </>
        )}
      </div>

      {/* Top chrome — optional, does not replace existing dashboard shell */}
      {topChrome && (
        <div
          style={{
            position: "sticky",
            top: 0,
            zIndex: 20,
            backdropFilter: "blur(24px) saturate(180%)",
            WebkitBackdropFilter: "blur(24px) saturate(180%)",
            background: "var(--enterprise-bg-glass)",
            borderBottom: "1px solid var(--enterprise-border)",
          }}
        >
          {topChrome}
        </div>
      )}

      {/* Command surface — optional overlay area */}
      {commandSurface && (
        <div style={{ position: "relative", zIndex: 30 }}>{commandSurface}</div>
      )}

      <div style={{ display: "flex", flex: 1, minHeight: 0, position: "relative" }}>
        {sidebar && (
          <aside
            aria-label="Enterprise sidebar"
            style={{
              width: 280,
              flexShrink: 0,
              borderRight: "1px solid var(--enterprise-border)",
              background: "var(--enterprise-bg-glass)",
              backdropFilter: "blur(24px) saturate(180%)",
              WebkitBackdropFilter: "blur(24px) saturate(180%)",
              position: "sticky",
              top: topChrome ? 0 : 0,
              alignSelf: "flex-start",
              height: topChrome ? "calc(100vh - 0px)" : "100vh",
              overflowY: "auto",
              zIndex: 10,
            }}
            className="enterprise-hide-mobile"
          >
            {sidebar}
          </aside>
        )}

        <main
          role="main"
          style={{
            flex: 1,
            minWidth: 0,
            maxWidth: sidebar ? `calc(${maxWidth} + 280px)` : maxWidth,
            width: "100%",
            margin: "0 auto",
            padding: contentPadding,
            position: "relative",
          }}
          aria-busy={isLoading ? true : undefined}
        >
          {isLoading ? (
            <div
              aria-label="Loading enterprise shell content"
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 16,
                padding: "24px 0",
              }}
            >
              <div
                style={{
                  height: 28,
                  width: "32%",
                  borderRadius: 8,
                  background: "linear-gradient(90deg, rgba(255,255,255,0.06) 25%, rgba(255,255,255,0.10) 50%, rgba(255,255,255,0.06) 75%)",
                  backgroundSize: "200% 100%",
                  animation: "enterprise-shimmer 1.2s ease-in-out infinite",
                }}
              />
              <div
                style={{
                  height: 16,
                  width: "48%",
                  borderRadius: 6,
                  background: "rgba(255,255,255,0.06)",
                }}
              />
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 16, marginTop: 8 }}>
                {[0, 1, 2].map((i) => (
                  <div
                    key={i}
                    style={{
                      height: 120,
                      borderRadius: "var(--enterprise-radius-lg)",
                      background: "rgba(255,255,255,0.04)",
                      border: "1px solid var(--enterprise-border)",
                    }}
                  />
                ))}
              </div>
            </div>
          ) : (
            children
          )}
        </main>
      </div>

      <style>{`
        @keyframes enterprise-shimmer {
          0% { background-position: -200% 0; }
          100% { background-position: 200% 0; }
        }
        @media (max-width: 768px) {
          .enterprise-shell main {
            padding: 16px !important;
          }
        }
        @media (prefers-reduced-motion: reduce) {
          .enterprise-shell * {
            animation-duration: 0.01ms !important;
          }
        }
      `}</style>
    </div>
  );
}
