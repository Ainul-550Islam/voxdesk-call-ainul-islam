/**
 * dashboard/src/api/billing.ts
 * Billing, subscription, usage and invoice client.
 *
 * Route source: app/api/billing_routes.py (router prefix `/api/billing`),
 * mounted unmodified in app/main.py. Response models are mirrored from
 * app/api/billing_routes.py (`BillingStatusOut`, `UsageOut`, `UsageMetricOut`,
 * `InvoiceOut`, `PlanOut`, `SessionOut`) — not guessed.
 *
 * The checkout/change-plan inputs deliberately carry only `plan_code` and
 * `interval`: the backend refuses amounts, price ids and tenant ids, so the
 * client cannot express them either.
 */
import { client } from './client';

export interface UsageMetric {
  metric: string;
  unit: string;
  included: number;
  used: number;
  remaining: number;
  overage: number;
  percent_used: number;
  overage_cents: number;
}

export interface Usage {
  billing_period: string;
  period_start: string;
  period_end: string;
  plan_code: string;
  metrics: UsageMetric[];
  estimated_overage_cents: number;
  currency: string;
}

export interface BillingStatus {
  plan_code: string;
  plan_name: string;
  subscription_status: string;
  interval: string;
  provider: string;
  current_period_start: string | null;
  current_period_end: string | null;
  trial_end: string | null;
  cancel_at_period_end: boolean;
  pending_plan_code: string | null;
  last_invoice_status: string | null;
  estimated_overage_cents: number;
  currency: string;
  usage?: Usage | null;
}

export interface Invoice {
  id: string;
  status: string;
  currency: string;
  amount_due_cents: number;
  amount_paid_cents: number;
  period_start: string | null;
  period_end: string | null;
  hosted_invoice_url: string | null;
}

export interface Plan {
  code: string;
  name: string;
  description: string;
  currency: string;
  monthly_price_cents: number;
  annual_price_cents: number | null;
  included_voice_minutes: number;
  included_sms_segments: number;
  overage_voice_minute_cents: number;
  overage_enabled: boolean;
  trial_days: number;
  features?: Record<string, unknown>;
  is_current?: boolean;
}

/**
 * `SessionOut` in app/api/billing_routes.py carries exactly one field.
 * Checkout, change-plan and portal all return a short-lived provider URL; there
 * is no session id to keep.
 */
export interface BillingSession {
  url: string;
}

export type BillingInterval = 'month' | 'year';

export function getBillingStatus(): Promise<BillingStatus> {
  return client.get<BillingStatus>('/api/billing');
}

export function getUsage(): Promise<Usage> {
  return client.get<Usage>('/api/billing/usage');
}

export function listInvoices(limit = 24): Promise<Invoice[]> {
  return client.get<Invoice[]>(`/api/billing/invoices?limit=${encodeURIComponent(String(limit))}`);
}

export function listPlans(): Promise<Plan[]> {
  return client.get<Plan[]>('/api/billing/plans');
}

export function startCheckout(planCode: string, interval: BillingInterval = 'month'): Promise<BillingSession> {
  return client.post<BillingSession>('/api/billing/checkout', { plan_code: planCode, interval });
}

export function changePlan(planCode: string, interval?: BillingInterval): Promise<BillingStatus> {
  return client.post<BillingStatus>('/api/billing/change-plan', {
    plan_code: planCode,
    ...(interval ? { interval } : {}),
  });
}

export function cancelSubscription(immediately = false, reason = ''): Promise<BillingStatus> {
  return client.post<BillingStatus>('/api/billing/cancel', { immediately, reason });
}

/** Opens the provider's hosted customer portal; the URL is short-lived and signed. */
export function openBillingPortal(): Promise<BillingSession> {
  return client.post<BillingSession>('/api/billing/portal');
}

export function reconcileBilling(): Promise<BillingStatus> {
  return client.post<BillingStatus>('/api/billing/reconcile');
}

/** `cents` → `$12.00` without inventing a currency symbol for an unknown code. */
export function formatMoney(cents: number, currency: string): string {
  const amount = cents / 100;
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency: currency.toUpperCase() }).format(amount);
  } catch {
    return `${amount.toFixed(2)} ${currency.toUpperCase()}`;
  }
}
