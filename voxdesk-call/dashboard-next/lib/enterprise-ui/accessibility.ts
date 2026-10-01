// File: lib/enterprise-ui/accessibility.ts — Accessibility helpers for labels, keyboard interaction, focus metadata and semantic state descriptions for enterprise UI

/**
 * VoxDesk Enterprise — Accessibility Helpers
 * Reusable, framework-agnostic helpers for ARIA, keyboard, focus, reduced-motion.
 * No fake data, no secrets.
 */

export type SemanticStatus =
  | "active"
  | "healthy"
  | "warning"
  | "degraded"
  | "disabled"
  | "pending"
  | "error"
  | "unknown"
  | "idle";

export type KeyboardInteraction = {
  readonly key: string;
  readonly description: string;
};

export const keyboardInteractions: Record<string, KeyboardInteraction> = {
  Enter: { key: "Enter", description: "Activate item" },
  Space: { key: " ", description: "Activate item" },
  Escape: { key: "Escape", description: "Dismiss or close" },
  ArrowUp: { key: "ArrowUp", description: "Navigate up" },
  ArrowDown: { key: "ArrowDown", description: "Navigate down" },
  ArrowLeft: { key: "ArrowLeft", description: "Navigate left" },
  ArrowRight: { key: "ArrowRight", description: "Navigate right" },
  Home: { key: "Home", description: "Go to first item" },
  End: { key: "End", description: "Go to last item" },
  Tab: { key: "Tab", description: "Move focus" },
};

export function getSemanticStatusLabel(status: SemanticStatus): string {
  const labels: Record<SemanticStatus, string> = {
    active: "Active",
    healthy: "Healthy",
    warning: "Warning",
    degraded: "Degraded",
    disabled: "Disabled",
    pending: "Pending",
    error: "Error",
    unknown: "Unknown status",
    idle: "Idle",
  };
  return labels[status] ?? "Unknown";
}

export function getStatusAriaProps(status: SemanticStatus): {
  "aria-label": string;
  "aria-live"?: "polite" | "assertive";
  role?: string;
} {
  const label = getSemanticStatusLabel(status);
  if (status === "error" || status === "degraded") {
    return { "aria-label": label, "aria-live": "assertive", role: "status" };
  }
  if (status === "pending" || status === "warning") {
    return { "aria-label": label, "aria-live": "polite", role: "status" };
  }
  return { "aria-label": label, role: "status" };
}

export function getMetricAriaLabel(params: {
  label: string;
  value?: string | number | null;
  unit?: string;
  delta?: string | null;
  status?: SemanticStatus;
}): string {
  const { label, value, unit, delta, status } = params;
  const parts: string[] = [label];

  if (value !== null && value !== undefined && String(value).trim() !== "" && String(value).trim() !== "—") {
    parts.push(`value ${value}${unit ? ` ${unit}` : ""}`);
  } else {
    parts.push("value unavailable");
  }

  if (delta) {
    parts.push(`change ${delta}`);
  }

  if (status) {
    parts.push(`status ${getSemanticStatusLabel(status)}`);
  }

  return parts.join(", ");
}

export function handleKeyboardActivation(
  event: React.KeyboardEvent | KeyboardEvent,
  callback: () => void
): void {
  const key = (event as React.KeyboardEvent).key;
  if (key === "Enter" || key === " ") {
    event.preventDefault();
    callback();
  }
}

export function handleArrowNavigation(
  event: React.KeyboardEvent,
  options: {
    onArrowUp?: () => void;
    onArrowDown?: () => void;
    onArrowLeft?: () => void;
    onArrowRight?: () => void;
    onHome?: () => void;
    onEnd?: () => void;
    onEscape?: () => void;
  }
): boolean {
  const { key } = event;
  let handled = false;

  switch (key) {
    case "ArrowUp":
      if (options.onArrowUp) {
        event.preventDefault();
        options.onArrowUp();
        handled = true;
      }
      break;
    case "ArrowDown":
      if (options.onArrowDown) {
        event.preventDefault();
        options.onArrowDown();
        handled = true;
      }
      break;
    case "ArrowLeft":
      if (options.onArrowLeft) {
        event.preventDefault();
        options.onArrowLeft();
        handled = true;
      }
      break;
    case "ArrowRight":
      if (options.onArrowRight) {
        event.preventDefault();
        options.onArrowRight();
        handled = true;
      }
      break;
    case "Home":
      if (options.onHome) {
        event.preventDefault();
        options.onHome();
        handled = true;
      }
      break;
    case "End":
      if (options.onEnd) {
        event.preventDefault();
        options.onEnd();
        handled = true;
      }
      break;
    case "Escape":
      if (options.onEscape) {
        event.preventDefault();
        options.onEscape();
        handled = true;
      }
      break;
    default:
      break;
  }

  return handled;
}

export function getFocusVisibleClassName(isFocusVisible: boolean): string {
  return isFocusVisible ? "enterprise-focus-visible" : "";
}

export function getScreenReaderOnlyClassName(): string {
  return "enterprise-sr-only";
}

export function getReducedMotionQuery(): string {
  return "(prefers-reduced-motion: reduce)";
}

export function isReducedMotionPreferred(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia === "undefined") {
    return false;
  }
  try {
    return window.matchMedia(getReducedMotionQuery()).matches;
  } catch {
    return false;
  }
}

export function getLiveRegionProps(politeness: "polite" | "assertive" | "off" = "polite"): {
  "aria-live": "polite" | "assertive" | "off";
  "aria-atomic": boolean;
  role: string;
} {
  return {
    "aria-live": politeness,
    "aria-atomic": true,
    role: "status",
  };
}

export function getButtonAriaProps(params: {
  label: string;
  disabled?: boolean;
  loading?: boolean;
  pressed?: boolean;
  expanded?: boolean;
  hasPopup?: boolean;
}): Record<string, unknown> {
  const { label, disabled, loading, pressed, expanded, hasPopup } = params;
  return {
    "aria-label": label,
    "aria-disabled": disabled || loading ? true : undefined,
    "aria-busy": loading ? true : undefined,
    "aria-pressed": typeof pressed === "boolean" ? pressed : undefined,
    "aria-expanded": typeof expanded === "boolean" ? expanded : undefined,
    "aria-haspopup": hasPopup ? true : undefined,
  };
}
