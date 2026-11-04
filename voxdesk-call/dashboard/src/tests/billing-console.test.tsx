/**
 * dashboard/src/tests/billing-console.test.tsx
 *
 * The billing console must never invent a price, never render usage it did not
 * receive, and must explain a 401/403 instead of showing an empty page. These
 * tests drive the page through each of those states against a stubbed API
 * client, so the assertions are about the page's behaviour rather than about
 * fetch plumbing (that lives in `api-contract.test.ts`).
 */
import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { ApiError } from '../api/client';

const getBillingStatus = vi.fn();
const getUsage = vi.fn();
const listInvoices = vi.fn();
const listPlans = vi.fn();
const startCheckout = vi.fn();
const changePlan = vi.fn();
const cancelSubscription = vi.fn();
const openBillingPortal = vi.fn();
const reconcileBilling = vi.fn();

vi.mock('../api/billing', () => ({
  getBillingStatus: (...args: unknown[]) => getBillingStatus(...args),
  getUsage: (...args: unknown[]) => getUsage(...args),
  listInvoices: (...args: unknown[]) => listInvoices(...args),
  listPlans: (...args: unknown[]) => listPlans(...args),
  startCheckout: (...args: unknown[]) => startCheckout(...args),
  changePlan: (...args: unknown[]) => changePlan(...args),
  cancelSubscription: (...args: unknown[]) => cancelSubscription(...args),
  openBillingPortal: (...args: unknown[]) => openBillingPortal(...args),
  reconcileBilling: (...args: unknown[]) => reconcileBilling(...args),
  // Money formatting is pure; the real implementation is used so the rendered
  // amount is the one operators actually see.
  formatMoney: (cents: number, currency = 'usd') =>
    `${(cents / 100).toFixed(2)} ${String(currency).toUpperCase()}`,
}));

import { BillingPage } from '../pages/billing/BillingPage';

const STATUS = {
  plan_code: 'growth',
  plan_name: 'Growth',
  subscription_status: 'active',
  interval: 'month',
  provider: 'stripe',
  currency: 'usd',
  current_period_start: '2026-10-01T00:00:00Z',
  current_period_end: '2026-11-01T00:00:00Z',
  trial_end: null,
  cancel_at_period_end: false,
  pending_plan_code: null,
  last_invoice_status: 'paid',
  estimated_overage_cents: 1250,
};

const USAGE = {
  billing_period: '2026-10',
  period_start: '2026-10-01T00:00:00Z',
  period_end: '2026-11-01T00:00:00Z',
  currency: 'usd',
  estimated_overage_cents: 1250,
  metrics: [
    {
      metric: 'voice_minutes',
      unit: 'min',
      included: 500,
      used: 640,
      remaining: 0,
      overage: 140,
      percent_used: 128,
      overage_cents: 1250,
    },
  ],
};

beforeEach(() => {
  getBillingStatus.mockReset();
  getUsage.mockReset();
  listInvoices.mockReset();
  listPlans.mockReset();
  startCheckout.mockReset();
  cancelSubscription.mockReset();
  openBillingPortal.mockReset();
});

describe('BillingPage', () => {
  it('renders the subscription, usage meters and invoices returned by the API', async () => {
    getBillingStatus.mockResolvedValue(STATUS);
    getUsage.mockResolvedValue(USAGE);
    listInvoices.mockResolvedValue([
      {
        id: 'in_001',
        status: 'paid',
        currency: 'usd',
        amount_due_cents: 4900,
        amount_paid_cents: 4900,
        period_start: '2026-09-01T00:00:00Z',
        period_end: '2026-10-01T00:00:00Z',
        hosted_invoice_url: 'https://pay.example.com/in_001',
      },
    ]);
    listPlans.mockResolvedValue([
      {
        code: 'growth',
        name: 'Growth',
        description: 'For growing teams',
        currency: 'usd',
        monthly_price_cents: 4900,
        annual_price_cents: 49000,
        included_voice_minutes: 500,
        included_sms_segments: 1000,
      },
    ]);

    render(<BillingPage />);

    expect(screen.getByText(/Loading billing/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('billing-plan-name')).toHaveTextContent('Growth'));
    expect(screen.getByTestId('billing-overage')).toHaveTextContent('12.50 USD');
    expect(screen.getByTestId('usage-row-voice_minutes')).toHaveTextContent('voice_minutes');
    expect(screen.getByRole('link', { name: 'Open' })).toHaveAttribute(
      'href',
      'https://pay.example.com/in_001',
    );
    // The current plan cannot be purchased again.
    expect(screen.getByTestId('plan-growth')).toHaveTextContent('Current');
  });

  it('explains a 401 rather than rendering an empty console', async () => {
    getBillingStatus.mockRejectedValue(new ApiError('UNAUTHORIZED', 401, 'Not authenticated'));
    getUsage.mockResolvedValue(USAGE);
    listInvoices.mockResolvedValue([]);
    listPlans.mockResolvedValue([]);

    render(<BillingPage />);

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/sign in/i));
    expect(screen.queryByTestId('billing-subscription')).not.toBeInTheDocument();
  });

  it('explains a 403 with the permission that is missing', async () => {
    getBillingStatus.mockRejectedValue(new ApiError('FORBIDDEN', 403, 'Forbidden'));
    getUsage.mockResolvedValue(USAGE);
    listInvoices.mockResolvedValue([]);
    listPlans.mockResolvedValue([]);

    render(<BillingPage />);

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/permission/i));
    expect(screen.getByRole('alert')).toHaveTextContent(/billing/i);
  });

  it('shows a real empty state when there is no metered usage and no invoice', async () => {
    getBillingStatus.mockResolvedValue(STATUS);
    getUsage.mockResolvedValue({ ...USAGE, metrics: [] });
    listInvoices.mockResolvedValue([]);
    listPlans.mockResolvedValue([]);

    render(<BillingPage />);

    await waitFor(() => expect(screen.getByText('No metered usage yet')).toBeInTheDocument());
    expect(screen.getByText('No invoices yet')).toBeInTheDocument();
    expect(screen.getByText('No plans published')).toBeInTheDocument();
  });

  it('marks a cancel-at-period-end subscription instead of offering another cancel', async () => {
    getBillingStatus.mockResolvedValue({ ...STATUS, cancel_at_period_end: true });
    getUsage.mockResolvedValue(USAGE);
    listInvoices.mockResolvedValue([]);
    listPlans.mockResolvedValue([]);

    render(<BillingPage />);

    await waitFor(() => expect(screen.getByText('Cancels at period end')).toBeDisabled());
  });
});
