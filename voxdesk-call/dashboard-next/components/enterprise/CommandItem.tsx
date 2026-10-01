// File: components/enterprise/CommandItem.tsx — Reusable command result item with icon, label, description, shortcut and disabled state for enterprise command surface

"use client";

import React from "react";

export interface CommandItemProps {
  readonly commandId: string;
  readonly label: string;
  readonly description?: string;
  readonly icon?: React.ReactNode;
  readonly shortcut?: string;
  readonly disabled?: boolean;
  readonly isActive?: boolean;
  readonly id?: string;
  readonly onSelect?: (id: string) => void;
  readonly className?: string;
  readonly style?: React.CSSProperties;
}

export function CommandItem({
  commandId,
  label,
  description,
  icon,
  shortcut,
  disabled = false,
  isActive = false,
  id,
  onSelect,
  className,
  style,
}: CommandItemProps): React.ReactElement {
  const handleClick = () => {
    if (disabled) return;
    onSelect?.(commandId);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (disabled) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onSelect?.(commandId);
    }
  };

  return (
    <div
      id={id}
      data-command-item="true"
      data-command-id={commandId}
      role="option"
      aria-selected={isActive}
      aria-disabled={disabled ? true : undefined}
      aria-label={`${label}${description ? `, ${description}` : ""}${shortcut ? `, shortcut ${shortcut}` : ""}`}
      tabIndex={disabled ? -1 : 0}
      className={["enterprise-command-item", className].filter(Boolean).join(" ")}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "10px 12px",
        borderRadius: "var(--enterprise-radius-sm)",
        background: isActive ? "var(--enterprise-surface-hover)" : "transparent",
        border: `1px solid ${isActive ? "var(--enterprise-border-strong)" : "transparent"}`,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.5 : 1,
        transition: "background 120ms ease, border-color 120ms ease",
        ...style,
      }}
    >
      {icon && (
        <span
          aria-hidden="true"
          style={{
            width: 32,
            height: 32,
            borderRadius: 8,
            background: isActive ? "var(--enterprise-surface-glass)" : "var(--enterprise-surface)",
            border: "1px solid var(--enterprise-border)",
            display: "grid",
            placeItems: "center",
            fontSize: 14,
            flexShrink: 0,
          }}
        >
          {icon}
        </span>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0, flex: 1 }}>
        <span
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: "var(--enterprise-text)",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {label}
        </span>
        {description && (
          <span
            style={{
              fontSize: 11,
              color: "var(--enterprise-text-secondary)",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {description}
          </span>
        )}
      </div>

      {shortcut && (
        <kbd
          aria-label={`Keyboard shortcut ${shortcut}`}
          style={{
            marginLeft: "auto",
            padding: "2px 6px",
            borderRadius: 6,
            background: "var(--enterprise-surface-glass)",
            border: "1px solid var(--enterprise-border)",
            fontSize: 10,
            fontFamily: "var(--enterprise-typography-mono, monospace)",
            color: "var(--enterprise-text-tertiary)",
            flexShrink: 0,
          }}
        >
          {shortcut}
        </kbd>
      )}
    </div>
  );
}
