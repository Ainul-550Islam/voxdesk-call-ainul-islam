// File: lib/enterprise-ui/visual-format.ts — Pure formatting helpers for safe metric display, percentages, durations and compact enterprise numbers using caller-provided values only, no fake data

/**
 * VoxDesk Enterprise — Visual Format Helpers
 * Pure functions, no side effects, no fake data.
 * Must not hide NaN/Infinity, must not fabricate 0 for missing data unless caller explicitly supplied 0.
 */

export type FormatOptions = {
  readonly maximumFractionDigits?: number;
  readonly minimumFractionDigits?: number;
  readonly locale?: string;
};

export type DeltaDirection = "up" | "down" | "neutral" | "unknown";

export type DeltaResult = {
  readonly text: string;
  readonly direction: DeltaDirection;
  readonly isPositive: boolean;
  readonly isNegative: boolean;
};

const UNAVAILABLE = "—";

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isValidInput(value: unknown): boolean {
  if (value === null || value === undefined) return false;
  if (typeof value === "number") {
    return Number.isFinite(value);
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (trimmed.length === 0) return false;
    if (trimmed === UNAVAILABLE) return false;
    return true;
  }
  return false;
}

/**
 * Safely formats a metric value. Returns "—" for missing/invalid without fabricating 0.
 * Preserves NaN/Infinity visibility by returning their string representation when explicitly passed as string,
 * but returns "—" for actual NaN/Infinity numeric values to avoid hiding them as 0.
 */
export function formatMetric(
  value: number | string | null | undefined,
  options: FormatOptions = {}
): string {
  const { maximumFractionDigits = 2, minimumFractionDigits, locale = "en-US" } = options;

  if (!isValidInput(value)) {
    return UNAVAILABLE;
  }

  if (typeof value === "string") {
    // Caller supplied string — respect it, but trim
    return value.trim();
  }

  // At this point value is number and finite (checked by isValidInput)
  // Explicitly guard NaN/Infinity even though isValidInput already filters
  if (!isFiniteNumber(value)) {
    return UNAVAILABLE;
  }

  try {
    return new Intl.NumberFormat(locale, {
      maximumFractionDigits,
      minimumFractionDigits,
    }).format(value);
  } catch {
    // Fallback if Intl fails
    return String(value);
  }
}

/**
 * Formats percentage. Expects 0-100 or 0-1 based on isRatio flag.
 * Does NOT fabricate fallback. Returns "—" for invalid.
 */
export function formatPercent(
  value: number | null | undefined,
  options: { isRatio?: boolean; maximumFractionDigits?: number; locale?: string } = {}
): string {
  const { isRatio = false, maximumFractionDigits = 1, locale = "en-US" } = options;

  if (value === null || value === undefined) return UNAVAILABLE;
  if (!isFiniteNumber(value)) return UNAVAILABLE;

  const percentValue = isRatio ? value * 100 : value;

  if (!isFiniteNumber(percentValue)) return UNAVAILABLE;

  try {
    return `${new Intl.NumberFormat(locale, { maximumFractionDigits }).format(percentValue)}%`;
  } catch {
    return `${percentValue}%`;
  }
}

/**
 * Formats duration in seconds to human readable. No fake data.
 */
export function formatDuration(
  seconds: number | null | undefined,
  options: { compact?: boolean } = {}
): string {
  const { compact = false } = options;
  if (seconds === null || seconds === undefined) return UNAVAILABLE;
  if (!isFiniteNumber(seconds)) return UNAVAILABLE;
  if (seconds < 0) return UNAVAILABLE;

  if (seconds < 60) {
    return compact ? `${Math.round(seconds)}s` : `${Math.round(seconds)} seconds`;
  }

  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.round(seconds % 60);

  if (seconds < 3600) {
    if (compact) {
      return remainingSeconds > 0 ? `${minutes}m ${remainingSeconds}s` : `${minutes}m`;
    }
    return remainingSeconds > 0 ? `${minutes} min ${remainingSeconds} sec` : `${minutes} min`;
  }

  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;

  if (compact) {
    return remainingMinutes > 0 ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
  }
  return remainingMinutes > 0 ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
}

/**
 * Compact number formatting (e.g., 1.2K, 3.4M). Uses caller-provided value only.
 */
export function formatCompactNumber(
  value: number | null | undefined,
  options: { maximumFractionDigits?: number; locale?: string } = {}
): string {
  const { maximumFractionDigits = 1, locale = "en-US" } = options;

  if (value === null || value === undefined) return UNAVAILABLE;
  if (!isFiniteNumber(value)) return UNAVAILABLE;

  try {
    return new Intl.NumberFormat(locale, {
      notation: "compact",
      maximumFractionDigits,
    }).format(value);
  } catch {
    if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(maximumFractionDigits)}M`;
    if (Math.abs(value) >= 1_000) return `${(value / 1_000).toFixed(maximumFractionDigits)}K`;
    return String(value);
  }
}

/**
 * Formats delta with direction. Does not fabricate 0.
 */
export function formatDelta(
  value: number | null | undefined,
  options: { maximumFractionDigits?: number; showSign?: boolean; locale?: string } = {}
): DeltaResult {
  const { maximumFractionDigits = 1, showSign = true, locale = "en-US" } = options;

  if (value === null || value === undefined) {
    return { text: UNAVAILABLE, direction: "unknown", isPositive: false, isNegative: false };
  }

  if (!isFiniteNumber(value)) {
    return { text: UNAVAILABLE, direction: "unknown", isPositive: false, isNegative: false };
  }

  if (value === 0) {
    return { text: "0%", direction: "neutral", isPositive: false, isNegative: false };
  }

  const direction: DeltaDirection = value > 0 ? "up" : value < 0 ? "down" : "neutral";
  const isPositive = value > 0;
  const isNegative = value < 0;

  let formatted: string;
  try {
    const abs = Math.abs(value);
    const num = new Intl.NumberFormat(locale, { maximumFractionDigits }).format(abs);
    formatted = showSign ? `${isPositive ? "+" : "-"}${num}%` : `${num}%`;
  } catch {
    formatted = showSign ? `${isPositive ? "+" : "-"}${Math.abs(value)}%` : `${Math.abs(value)}%`;
  }

  return { text: formatted, direction, isPositive, isNegative };
}

/**
 * Returns true if value should be considered unavailable (null, undefined, NaN, Infinity, empty string, "—")
 */
export function isUnavailable(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (value === UNAVAILABLE) return true;
  if (typeof value === "number") return !Number.isFinite(value);
  if (typeof value === "string") return value.trim().length === 0 || value.trim() === UNAVAILABLE;
  return false;
}

export const UNAVAILABLE_SYMBOL = UNAVAILABLE;
