// File: components/enterprise/CommandSurface.tsx — Enterprise command/search surface for later navigation and control-plane integration, with keyboard handling but no fake actions

"use client";

import React, { useCallback, useEffect, useId, useRef, useState } from "react";
import { handleArrowNavigation } from "@/lib/enterprise-ui/accessibility";

export interface CommandSurfaceProps {
  readonly open: boolean;
  readonly query: string;
  readonly onQueryChange: (query: string) => void;
  readonly onOpenChange: (open: boolean) => void;
  readonly onSelect?: (id: string) => void;
  readonly placeholder?: string;
  readonly ariaLabel?: string;
  readonly children?: React.ReactNode;
  readonly className?: string;
  readonly style?: React.CSSProperties;
}

export function CommandSurface({
  open,
  query,
  onQueryChange,
  onOpenChange,
  onSelect,
  placeholder = "Search commands, agents, workflows…",
  ariaLabel = "Command surface",
  children,
  className,
  style,
}: CommandSurfaceProps): React.ReactElement | null {
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const listboxId = useId();
  const inputId = useId();

  const itemCount = React.Children.count(children);

  useEffect(() => {
    if (open) {
      // Focus input when opened
      requestAnimationFrame(() => {
        inputRef.current?.focus();
      });
      setActiveIndex(0);
    }
  }, [open]);

  useEffect(() => {
    // Reset active index when query changes or children count changes
    setActiveIndex(0);
  }, [query, itemCount]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const handled = handleArrowNavigation(e, {
        onArrowUp: () => {
          setActiveIndex((prev) => (prev <= 0 ? Math.max(0, itemCount - 1) : prev - 1));
        },
        onArrowDown: () => {
          setActiveIndex((prev) => (prev >= itemCount - 1 ? 0 : prev + 1));
        },
        onHome: () => setActiveIndex(0),
        onEnd: () => setActiveIndex(Math.max(0, itemCount - 1)),
        onEscape: () => {
          onOpenChange(false);
        },
      });

      if (handled) return;

      if (e.key === "Enter") {
        const list = listRef.current;
        if (!list) return;
        const items = list.querySelectorAll<HTMLElement>("[data-command-item]");
        const active = items[activeIndex] as HTMLElement | undefined;
        if (active) {
          const id = active.getAttribute("data-command-id");
          if (id && onSelect) {
            e.preventDefault();
            onSelect(id);
          } else {
            active.click();
          }
        }
      }
    },
    [itemCount, activeIndex, onOpenChange, onSelect]
  );

  const handleOverlayClick = useCallback(
    (e: React.MouseEvent) => {
      if (e.target === e.currentTarget) {
        onOpenChange(false);
      }
    },
    [onOpenChange]
  );

  useEffect(() => {
    if (!open) return;
    const handleDocKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onOpenChange(false);
      }
    };
    document.addEventListener("keydown", handleDocKey);
    return () => document.removeEventListener("keydown", handleDocKey);
  }, [open, onOpenChange]);

  if (!open) return null;

  return (
    <div
      role="presentation"
      onClick={handleOverlayClick}
      className={["enterprise-command-overlay", className].filter(Boolean).join(" ")}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 60,
        background: "rgba(0,0,0,0.48)",
        backdropFilter: "blur(8px)",
        WebkitBackdropFilter: "blur(8px)",
        display: "grid",
        placeItems: "start center",
        paddingTop: "20vh",
        paddingLeft: 16,
        paddingRight: 16,
        ...style,
      }}
    >
      <div
        className="enterprise-command-surface"
        role="combobox"
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-owns={listboxId}
        aria-label={ariaLabel}
        style={{ width: "100%", maxWidth: 640 }}
        onKeyDown={handleKeyDown}
      >
        <div style={{ position: "relative", display: "flex", alignItems: "center" }}>
          <span
            aria-hidden="true"
            style={{
              position: "absolute",
              left: 16,
              color: "var(--enterprise-text-tertiary)",
              fontSize: 14,
            }}
          >
            ⌘
          </span>
          <input
            ref={inputRef}
            id={inputId}
            className="enterprise-command-surface__input enterprise-focus-ring"
            type="text"
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            placeholder={placeholder}
            aria-autocomplete="list"
            aria-controls={listboxId}
            aria-activedescendant={itemCount > 0 ? `command-item-${activeIndex}` : undefined}
            autoComplete="off"
            spellCheck={false}
          />
          <button
            type="button"
            aria-label="Close command surface"
            onClick={() => onOpenChange(false)}
            style={{
              position: "absolute",
              right: 12,
              width: 28,
              height: 28,
              borderRadius: 8,
              border: "1px solid var(--enterprise-border)",
              background: "var(--enterprise-surface-glass)",
              color: "var(--enterprise-text-secondary)",
              display: "grid",
              placeItems: "center",
              cursor: "pointer",
              fontSize: 12,
            }}
          >
            Esc
          </button>
        </div>

        <div
          ref={listRef}
          id={listboxId}
          role="listbox"
          aria-label="Command results"
          className="enterprise-command-surface__list"
        >
          {itemCount === 0 ? (
            <div
              style={{
                padding: "24px 16px",
                textAlign: "center",
                color: "var(--enterprise-text-secondary)",
                fontSize: 13,
              }}
              role="status"
              aria-live="polite"
            >
              <div style={{ fontWeight: 600, marginBottom: 4 }}>No results</div>
              <div style={{ fontSize: 12, color: "var(--enterprise-text-tertiary)" }}>
                Try a different search or check your query
              </div>
            </div>
          ) : (
            React.Children.map(children, (child, index) => {
              if (!React.isValidElement(child)) return child;
              return React.cloneElement(child as React.ReactElement<{ isActive?: boolean; id?: string }>, {
                isActive: index === activeIndex,
                id: `command-item-${index}`,
              });
            })
          )}
        </div>

        <div
          style={{
            padding: "8px 12px",
            borderTop: "1px solid var(--enterprise-border)",
            display: "flex",
            alignItems: "center",
            gap: 12,
            fontSize: 11,
            color: "var(--enterprise-text-tertiary)",
          }}
          aria-hidden="true"
        >
          <span>↑↓ Navigate</span>
          <span>↵ Select</span>
          <span>Esc Close</span>
        </div>
      </div>
    </div>
  );
}
