/**
 * Types consumed by `lib/api.ts` for analytics and forecasting.
 *
 * `CallAnalyticsResponse` mirrors `app.api.analytics_routes.CallAnalyticsOut`
 * (GET `/api/analytics/calls`). `UsagePoint` mirrors
 * `app.analytics.forecast.UsagePoint`, the series item posted to
 * `/api/forecast` and `/api/forecast/backtest`.
 */

export interface AnalyticsWindow {
  label: string;
  start: string;
  end: string;
  timezone: string;
  days: number;
}

export interface CallAnalyticsSeriesPoint {
  date: string;
  answered: number;
  missed: number;
  failed: number;
  booked: number;
  transferred: number;
  minutes: number;
}

export interface CallAnalyticsResponse {
  window: AnalyticsWindow;
  totals: Record<string, unknown>;
  by_status: Record<string, unknown>;
  by_direction: Record<string, unknown>;
  by_hour: Array<Record<string, unknown>>;
  series: CallAnalyticsSeriesPoint[];
}

export interface UsagePoint {
  period: string;
  value: number;
}
