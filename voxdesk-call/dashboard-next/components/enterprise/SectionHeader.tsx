// File: components/enterprise/SectionHeader.tsx — Consistent enterprise page-header component for title, description, breadcrumbs and action slots with mobile stacking

"use client";

import React from "react";

export interface BreadcrumbItem {
  readonly label: string;
  readonly href?: string;
  readonly onClick?: () => void;
}

export interface SectionHeaderProps {
  readonly eyebrow?: string;
  readonly title: string;
  readonly description?: string;
  readonly breadcrumbs?: readonly BreadcrumbItem[];
  readonly primaryAction?: React.ReactNode;
  readonly secondaryAction?: React.ReactNode;
  readonly actions?: React.ReactNode;
  readonly className?: string;
  readonly style?: React.CSSProperties;
  readonly titleId?: string;
}

export function SectionHeader({
  eyebrow,
  title,
  description,
  breadcrumbs,
  primaryAction,
  secondaryAction,
  actions,
  className,
  style,
  titleId,
}: SectionHeaderProps): React.ReactElement {
  const generatedId = React.useId();
  const effectiveTitleId = titleId ?? `section-header-${generatedId}`;

  return (
    <header
      className={["enterprise-section-header", className].filter(Boolean).join(" ")}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 12,
        marginBottom: 24,
        ...style,
      }}
      aria-labelledby={effectiveTitleId}
    >
      {breadcrumbs && breadcrumbs.length > 0 && (
        <nav aria-label="Breadcrumb" style={{ fontSize: 11 }}>
          <ol
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              listStyle: "none",
              margin: 0,
              padding: 0,
              flexWrap: "wrap",
            }}
          >
            {breadcrumbs.map((crumb, index) => {
              const isLast = index === breadcrumbs.length - 1;
              return (
                <li key={`${crumb.label}-${index}`} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  {crumb.href && !isLast ? (
                    <a
                      href={crumb.href}
                      onClick={crumb.onClick ? (e) => { e.preventDefault(); crumb.onClick?.(); } : undefined}
                      style={{
                        color: "var(--enterprise-text-secondary)",
                        textDecoration: "none",
                        fontWeight: 500,
                      }}
                      className="enterprise-focus-ring"
                    >
                      {crumb.label}
                    </a>
                  ) : (
                    <span
                      style={{
                        color: isLast ? "var(--enterprise-text)" : "var(--enterprise-text-secondary)",
                        fontWeight: isLast ? 600 : 500,
                      }}
                      aria-current={isLast ? "page" : undefined}
                    >
                      {crumb.label}
                    </span>
                  )}
                  {!isLast && (
                    <span aria-hidden="true" style={{ color: "var(--enterprise-text-tertiary)" }}>
                      /
                    </span>
                  )}
                </li>
              );
            })}
          </ol>
        </nav>
      )}

      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          gap: 16,
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 0, flex: 1 }}>
          {eyebrow && (
            <span
              style={{
                fontSize: 11,
                fontWeight: 700,
                textTransform: "uppercase",
                letterSpacing: "0.08em",
                color: "var(--enterprise-accent-light)",
              }}
            >
              {eyebrow}
            </span>
          )}
          <h1
            id={effectiveTitleId}
            style={{
              margin: 0,
              fontSize: 28,
              fontWeight: 800,
              letterSpacing: "-0.03em",
              lineHeight: 1.1,
              color: "var(--enterprise-text)",
            }}
          >
            {title}
          </h1>
          {description && (
            <p
              style={{
                margin: 0,
                fontSize: 13,
                lineHeight: 1.6,
                color: "var(--enterprise-text-secondary)",
                maxWidth: 640,
              }}
            >
              {description}
            </p>
          )}
        </div>

        {(primaryAction || secondaryAction || actions) && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              flexShrink: 0,
              flexWrap: "wrap",
            }}
          >
            {secondaryAction}
            {primaryAction}
            {actions}
          </div>
        )}
      </div>

      <style>{`
        @media (max-width: 768px) {
          .enterprise-section-header {
            gap: 10px;
          }
          .enterprise-section-header h1 {
            font-size: 22px !important;
          }
        }
      `}</style>
    </header>
  );
}
