/**
 * Formatting and derivation helpers for the platform console.
 *
 * Kept free of React and of `fetch` so every rule that a reader would want to verify
 * — how a fraction becomes a percentage, what "attention" means for a domain, when a
 * schedule counts as unpublished — is a plain function with a unit test.
 *
 * The naming rule that matters most: anything ending in `Fraction` is 0–1 and anything
 * ending in `Percent` is 0–100. Accuracy metrics arrive as fractions (WAPE 0.128 is a
 * 12.8% error); a console that prints 0.128% next to a number called WAPE is wrong by
 * two orders of magnitude and nobody notices for months.
 */

export type Tone = "ok" | "warn" | "bad" | "neutral" | "info";

/** `0.128` → `"12.8%"`. Non-finite input renders as an em dash, never as `NaN%`. */
export function formatPercent(fraction: number | null | undefined, digits = 1): string {
  if (fraction === null || fraction === undefined || !Number.isFinite(fraction)) return "—";
  return `${(fraction * 100).toFixed(digits)}%`;
}

/** A signed fraction as a percentage, for bias: `-0.042` → `"-4.2%"`. */
export function formatSignedPercent(fraction: number | null | undefined, digits = 1): string {
  if (fraction === null || fraction === undefined || !Number.isFinite(fraction)) return "—";
  const value = fraction * 100;
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)}%`;
}

export function formatNumber(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return value.toFixed(digits);
}

/** Minutes since local midnight → `"09:30"`. Accepts values past 1440 (overnight). */
export function clockFromMinute(minute: number | null | undefined): string {
  if (minute === null || minute === undefined || !Number.isFinite(minute)) return "—";
  const wrapped = ((Math.round(minute) % 1440) + 1440) % 1440;
  const hours = Math.floor(wrapped / 60);
  const minutes = wrapped % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
}

/** `450` minutes → `"7h 30m"`; `480` → `"8h"`; `0` → `"0m"`. */
export function durationLabel(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined || !Number.isFinite(minutes)) return "—";
  const total = Math.max(0, Math.round(minutes));
  const hours = Math.floor(total / 60);
  const rest = total % 60;
  if (hours && rest) return `${hours}h ${rest}m`;
  if (hours) return `${hours}h`;
  return `${rest}m`;
}

/** ISO timestamp → `"26 Oct 2026, 14:05"` in the browser's zone; `—` when absent. */
export function timestampLabel(iso: string | null | undefined): string {
  if (!iso) return "—";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** `"2026-10-05"` → `"Mon 5 Oct"`, derived in UTC so the label cannot shift a day. */
export function dayLabel(isoDate: string | null | undefined): string {
  if (!isoDate) return "—";
  const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate);
  if (!parts) return isoDate;
  const [, year, month, day] = parts;
  const parsed = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)));
  if (Number.isNaN(parsed.getTime())) return isoDate;
  return parsed.toLocaleDateString("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
}

// ------------------------------------------------------------------- tones ---

const DOMAIN_TONES: Record<string, Tone> = {
  // Only `active` is routable: the TLS ask refuses everything else, so the console
  // must not paint `issuing_cert` green and invite an operator to declare victory.
  active: "ok",
  issuing_cert: "info",
  pending_dns: "warn",
  failed: "bad",
  disabled: "neutral",
};

export function domainTone(status: string): Tone {
  return DOMAIN_TONES[status] ?? "neutral";
}

/** Human wording for a domain status; unknown statuses are shown verbatim. */
export function domainStatusLabel(status: string): string {
  switch (status) {
    case "pending_dns":
      return "Waiting for DNS";
    case "issuing_cert":
      return "Issuing certificate";
    case "active":
      return "Live";
    case "failed":
      return "Failed";
    case "disabled":
      return "Disabled";
    default:
      return status;
  }
}

export function scheduleTone(status: string): Tone {
  switch (status) {
    case "published":
      return "ok";
    case "draft":
      return "warn";
    case "archived":
      return "neutral";
    default:
      return "neutral";
  }
}

export function deliveryTone(status: string): Tone {
  switch (status) {
    case "sent":
      return "ok";
    case "queued":
      return "info";
    case "dropped":
      return "warn";
    case "failed":
      return "bad";
    default:
      return "neutral";
  }
}

export function timeOffTone(status: string): Tone {
  switch (status) {
    case "approved":
      return "ok";
    case "requested":
      return "warn";
    case "denied":
    case "cancelled":
      return "neutral";
    default:
      return "neutral";
  }
}

export function certificateTone(state: string, renewalDue: boolean): Tone {
  if (state === "failed" || state === "expired") return "bad";
  if (renewalDue || state === "expiring") return "warn";
  if (state === "issued") return "ok";
  return "info";
}

// ------------------------------------------------------------- derivations ---

/**
 * The status an operator should care about first.
 *
 * Order of gravity is fixed: a failure outranks a waiting domain, which outranks a
 * live one. With no domains the answer is `none` — "no domains" is a state, not a
 * problem, and the UI says so instead of showing a green tick over nothing.
 */
export function worstDomainStatus(statuses: string[]): "failed" | "pending" | "active" | "none" {
  if (statuses.length === 0) return "none";
  if (statuses.some((status) => status === "failed")) return "failed";
  if (statuses.some((status) => status === "pending_dns" || status === "issuing_cert")) {
    return "pending";
  }
  if (statuses.every((status) => status === "active")) return "active";
  return "pending";
}

/** Which days the rota under-covers, given the solver report's shortfall count. */
export function coverageVerdict(ratio: number | null | undefined): {
  tone: Tone;
  label: string;
} {
  if (ratio === null || ratio === undefined || !Number.isFinite(ratio)) {
    return { tone: "neutral", label: "Not solved yet" };
  }
  if (ratio >= 0.999) return { tone: "ok", label: "Fully covered" };
  if (ratio >= 0.95) return { tone: "warn", label: "Nearly covered" };
  return { tone: "bad", label: "Short-staffed" };
}

export interface DailyTotal {
  date: string;
  expected: number;
}

/** Daily totals → the peak day and the mean, for the forecast header. */
export function summariseDaily(totals: Record<string, number> | null | undefined): {
  days: DailyTotal[];
  peak: DailyTotal | null;
  mean: number | null;
  total: number | null;
} {
  if (!totals) return { days: [], peak: null, mean: null, total: null };
  const days = Object.entries(totals)
    .map(([date, expected]) => ({ date, expected }))
    .filter((day) => Number.isFinite(day.expected))
    .sort((left, right) => left.date.localeCompare(right.date));
  if (days.length === 0) return { days: [], peak: null, mean: null, total: null };
  const total = days.reduce((sum, day) => sum + day.expected, 0);
  const peak = days.reduce((best, day) => (day.expected > best.expected ? day : best), days[0]);
  return { days, peak, mean: total / days.length, total };
}

/**
 * Horizontal bar width for a day's volume, as a percentage of the busiest day.
 *
 * Zero-volume days get width 0 rather than a minimum sliver: a closed day drawing a
 * visible bar reads as real demand.
 */
export function barWidthPercent(value: number, peak: number): number {
  if (!Number.isFinite(value) || !Number.isFinite(peak) || peak <= 0 || value <= 0) return 0;
  return Math.min(100, Math.round((value / peak) * 100));
}

/**
 * Minutes of *paid* time a template is on the clock for.
 *
 * Paid breaks are still paid; only the unpaid break comes off the length. Getting this
 * backwards overstates or understates a rota by the break length, which is exactly the
 * argument that ends a scheduling rollout.
 */
export function paidMinutes(template: {
  length_minutes: number;
  unpaid_break_minutes: number;
}): number {
  return Math.max(0, template.length_minutes - template.unpaid_break_minutes);
}

/** Templates whose paid time exceeds the weekly cap cannot be used as-is. */
export function templateViolations(
  template: { name: string; length_minutes: number; unpaid_break_minutes: number },
  rules: { max_shift_hours: number } | null,
): string[] {
  const problems: string[] = [];
  if (!rules) return problems;
  const paid = paidMinutes(template);
  if (paid > rules.max_shift_hours * 60) {
    problems.push(
      `${template.name} is ${durationLabel(paid)} paid, over the ${durationLabel(
        rules.max_shift_hours * 60,
      )} shift cap`,
    );
  }
  return problems;
}

/** Quiet hours window → `"22:00–07:00"`, or `"Not set"` when the device is always on. */
export function quietHoursLabel(window: Record<string, unknown> | null | undefined): string {
  if (!window || Object.keys(window).length === 0) return "Not set";
  const start = typeof window.start_minute === "number" ? window.start_minute : null;
  const end = typeof window.end_minute === "number" ? window.end_minute : null;
  if (start === null || end === null) return "Not set";
  return `${clockFromMinute(start)}–${clockFromMinute(end)}`;
}

/** Contrast pairs that fail, from a `theme/validate` report. */
export function contrastFailures(report: {
  pairs?: Array<{ foreground: string; background: string; ratio: number; minimum: number; passed: boolean }>;
  failures?: string[];
} | null | undefined): string[] {
  if (!report) return [];
  if (Array.isArray(report.failures) && report.failures.length) return report.failures;
  return (report.pairs ?? [])
    .filter((pair) => !pair.passed)
    .map(
      (pair) =>
        `${pair.foreground} on ${pair.background} is ${pair.ratio.toFixed(2)}:1, needs ${pair.minimum}:1`,
    );
}

/** Sorted, de-duplicated template skill keys present in a set of templates. */
export function skillKeys(templates: Array<{ required_skill_key: string }>): string[] {
  return Array.from(
    new Set(templates.map((template) => template.required_skill_key).filter(Boolean)),
  ).sort();
}

/** Delivery statuses → `"3 sent · 1 dropped"`, ordered by the platform's own states. */
export function deliverySummaryLabel(summary: Record<string, number> | null | undefined): string {
  if (!summary) return "No deliveries";
  const order = ["sent", "queued", "dropped", "failed"];
  const parts = order
    .filter((key) => (summary[key] ?? 0) > 0)
    .map((key) => `${summary[key]} ${key}`);
  const extras = Object.keys(summary)
    .filter((key) => !order.includes(key) && summary[key] > 0)
    .map((key) => `${summary[key]} ${key}`);
  const all = [...parts, ...extras];
  return all.length ? all.join(" · ") : "No deliveries";
}

/** Shifts grouped by tenant-local day, for the rota grid. */
export function shiftsByDay(
  shifts: Array<{ starts_at_local: string }>,
): Array<{ date: string; count: number }> {
  const counts = new Map<string, number>();
  for (const shift of shifts) {
    const date = shift.starts_at_local.slice(0, 10);
    counts.set(date, (counts.get(date) ?? 0) + 1);
  }
  return Array.from(counts.entries())
    .map(([date, count]) => ({ date, count }))
    .sort((left, right) => left.date.localeCompare(right.date));
}
