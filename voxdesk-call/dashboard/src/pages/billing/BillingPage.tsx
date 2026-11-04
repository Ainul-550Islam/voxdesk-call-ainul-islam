/**
 * dashboard/src/pages/billing/BillingPage.tsx
 * Plan, usage meters, invoices and the hosted payment surface.
 *
 * Data: `api/billing.ts` → `/api/billing`, `/api/billing/usage`,
 * `/api/billing/invoices`, `/api/billing/plans` (app/api/billing_routes.py).
 *
 * All money is formatted from integer cents with the tenant's own currency code;
 * nothing is recomputed in the browser. Plan changes go through
 * `POST /api/billing/checkout`, which accepts only a plan code and an interval —
 * the price is resolved server-side, so this page never posts an amount.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  BillingStatus,
  Invoice,
  Plan,
  Usage,
  cancelSubscription,
  formatMoney,
  getBillingStatus,
  getUsage,
  listInvoices,
  listPlans,
  openBillingPortal,
  startCheckout,
} from '../../api/billing';
import {
  Badge,
  ConsoleCard,
  EmptyState,
  ErrorState,
  LoadingState,
  buttonStyle,
  formatDate,
  subtleButtonStyle,
  tableStyle,
  tdStyle,
  thStyle,
} from '../../components/layout/ConsoleStates';

export function BillingPage() {
  const [status, setStatus] = useState<BillingStatus | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // One round trip per surface; a failure on any of them is fatal for the
      // page because a plan without usage or invoices is not a billing view.
      const [statusResult, usageResult, invoiceResult, planResult] = await Promise.all([
        getBillingStatus(),
        getUsage(),
        listInvoices(),
        listPlans(),
      ]);
      setStatus(statusResult);
      setUsage(usageResult);
      setInvoices(invoiceResult);
      setPlans(planResult);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const currency = status?.currency ?? usage?.currency ?? 'usd';

  const planRows = useMemo(
    () => plans.map((plan) => ({ ...plan, isCurrent: plan.is_current ?? plan.code === status?.plan_code })),
    [plans, status?.plan_code],
  );

  async function runAction(key: string, action: () => Promise<void>) {
    setBusy(key);
    setActionError(null);
    try {
      await action();
    } catch (e) {
      setActionError(e);
    } finally {
      setBusy(null);
    }
  }

  function checkout(planCode: string, interval: 'month' | 'year') {
    void runAction(`checkout:${planCode}`, async () => {
      const session = await startCheckout(planCode, interval);
      if (session.url) window.location.assign(session.url);
    });
  }

  function openPortal() {
    void runAction('portal', async () => {
      const session = await openBillingPortal();
      if (session.url) window.location.assign(session.url);
    });
  }

  function cancel() {
    void runAction('cancel', async () => {
      const next = await cancelSubscription(false);
      setStatus(next);
    });
  }

  if (loading) return <LoadingState label="Loading billing…" />;

  return (
    <main data-testid="billing-page" style={{ padding: 20, color: '#e9eef5' }}>
      <h1 style={{ fontSize: 20, margin: '0 0 4px' }}>Billing</h1>
      <p style={{ margin: '0 0 16px', fontSize: 12.5, color: '#8fa3b8' }}>
        Plan, metered usage and invoices for this workspace, read from the billing service.
      </p>

      <ErrorState
        error={error}
        onRetry={() => void load()}
        forbiddenHint="This account cannot read billing data. An owner or admin must grant the billing read permission."
      />
      <ErrorState error={actionError} />

      {status && !error && (
        <ConsoleCard
          title="Subscription"
          testId="billing-subscription"
          actions={
            <div style={{ display: 'flex', gap: 8 }}>
              <button style={subtleButtonStyle} onClick={openPortal} disabled={busy === 'portal'}>
                {busy === 'portal' ? 'Opening…' : 'Payment method'}
              </button>
              <button
                style={subtleButtonStyle}
                onClick={cancel}
                disabled={busy === 'cancel' || status.cancel_at_period_end}
              >
                {status.cancel_at_period_end ? 'Cancels at period end' : busy === 'cancel' ? 'Cancelling…' : 'Cancel plan'}
              </button>
            </div>
          }
        >
          <dl style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(180px,1fr))', gap: 12, margin: 0 }}>
            <div>
              <dt style={{ fontSize: 11, color: '#8fa3b8' }}>Plan</dt>
              <dd style={{ margin: '2px 0 0', fontSize: 15 }} data-testid="billing-plan-name">
                {status.plan_name} <span style={{ color: '#8fa3b8', fontSize: 12 }}>({status.plan_code})</span>
              </dd>
            </div>
            <div>
              <dt style={{ fontSize: 11, color: '#8fa3b8' }}>Status</dt>
              <dd style={{ margin: '2px 0 0' }}>
                <Badge tone={status.subscription_status === 'active' ? 'ok' : 'warn'}>{status.subscription_status}</Badge>
                <span style={{ marginLeft: 8, fontSize: 12, color: '#8fa3b8' }}>{status.interval}ly · {status.provider}</span>
              </dd>
            </div>
            <div>
              <dt style={{ fontSize: 11, color: '#8fa3b8' }}>Current period</dt>
              <dd style={{ margin: '2px 0 0', fontSize: 12.5 }}>
                {formatDate(status.current_period_start)} → {formatDate(status.current_period_end)}
              </dd>
            </div>
            <div>
              <dt style={{ fontSize: 11, color: '#8fa3b8' }}>Trial ends</dt>
              <dd style={{ margin: '2px 0 0', fontSize: 12.5 }}>{formatDate(status.trial_end)}</dd>
            </div>
            <div>
              <dt style={{ fontSize: 11, color: '#8fa3b8' }}>Estimated overage</dt>
              <dd style={{ margin: '2px 0 0', fontSize: 12.5 }} data-testid="billing-overage">
                {formatMoney(status.estimated_overage_cents, currency)}
              </dd>
            </div>
            {status.pending_plan_code && (
              <div>
                <dt style={{ fontSize: 11, color: '#8fa3b8' }}>Pending plan</dt>
                <dd style={{ margin: '2px 0 0', fontSize: 12.5 }}>{status.pending_plan_code}</dd>
              </div>
            )}
            <div>
              <dt style={{ fontSize: 11, color: '#8fa3b8' }}>Last invoice</dt>
              <dd style={{ margin: '2px 0 0', fontSize: 12.5 }}>{status.last_invoice_status ?? '—'}</dd>
            </div>
          </dl>
        </ConsoleCard>
      )}

      {usage && !error && (
        <ConsoleCard
          title="Usage this period"
          testId="billing-usage"
          description={`${usage.billing_period} · ${formatDate(usage.period_start)} → ${formatDate(usage.period_end)}`}
        >
          {usage.metrics.length === 0 ? (
            <EmptyState title="No metered usage yet" detail="Metered metrics appear once a call or message is billed." />
          ) : (
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={thStyle}>Metric</th>
                  <th style={thStyle}>Used</th>
                  <th style={thStyle}>Included</th>
                  <th style={thStyle}>Remaining</th>
                  <th style={thStyle}>Overage</th>
                  <th style={thStyle}>Meter</th>
                  <th style={thStyle}>Overage cost</th>
                </tr>
              </thead>
              <tbody>
                {usage.metrics.map((metric) => {
                  const pct = Math.max(0, Math.min(100, metric.percent_used));
                  return (
                    <tr key={metric.metric} data-testid={`usage-row-${metric.metric}`}>
                      <td style={tdStyle}>{metric.metric}</td>
                      <td style={tdStyle}>
                        {metric.used} {metric.unit}
                      </td>
                      <td style={tdStyle}>{metric.included}</td>
                      <td style={tdStyle}>{metric.remaining}</td>
                      <td style={tdStyle}>{metric.overage}</td>
                      <td style={{ ...tdStyle, minWidth: 120 }}>
                        <div style={{ height: 6, borderRadius: 3, background: '#141c26' }}>
                          <div
                            style={{ height: 6, borderRadius: 3, width: `${pct}%`, background: pct >= 100 ? '#e05252' : pct >= 80 ? '#e0a552' : '#3fbf7f' }}
                          />
                        </div>
                        <span style={{ fontSize: 10.5, color: '#8fa3b8' }}>{pct.toFixed(1)}%</span>
                      </td>
                      <td style={tdStyle}>{formatMoney(metric.overage_cents, currency)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </ConsoleCard>
      )}

      <ConsoleCard title="Invoices" testId="billing-invoices">
        {invoices.length === 0 ? (
          <EmptyState title="No invoices yet" detail="An invoice is created when the provider closes a billing period." />
        ) : (
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={thStyle}>Invoice</th>
                <th style={thStyle}>Status</th>
                <th style={thStyle}>Due</th>
                <th style={thStyle}>Paid</th>
                <th style={thStyle}>Period</th>
                <th style={thStyle}>Hosted</th>
              </tr>
            </thead>
            <tbody>
              {invoices.map((invoice) => (
                <tr key={invoice.id}>
                  <td style={tdStyle}>{invoice.id}</td>
                  <td style={tdStyle}>
                    <Badge tone={invoice.status === 'paid' ? 'ok' : invoice.status === 'open' ? 'warn' : 'neutral'}>
                      {invoice.status}
                    </Badge>
                  </td>
                  <td style={tdStyle}>{formatMoney(invoice.amount_due_cents, invoice.currency)}</td>
                  <td style={tdStyle}>{formatMoney(invoice.amount_paid_cents, invoice.currency)}</td>
                  <td style={tdStyle}>
                    {formatDate(invoice.period_start)} → {formatDate(invoice.period_end)}
                  </td>
                  <td style={tdStyle}>
                    {invoice.hosted_invoice_url ? (
                      <a href={invoice.hosted_invoice_url} target="_blank" rel="noreferrer" style={{ color: '#6ea8ff' }}>
                        Open
                      </a>
                    ) : (
                      '—'
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </ConsoleCard>

      <ConsoleCard
        title="Plans"
        testId="billing-plans"
        description="Prices come from the server-side catalogue; checkout sends only the plan code and interval."
      >
        {planRows.length === 0 ? (
          <EmptyState title="No plans published" detail="The plan catalogue is empty for this deployment." />
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(220px,1fr))', gap: 12 }}>
            {planRows.map((plan) => (
              <div
                key={plan.code}
                data-testid={`plan-${plan.code}`}
                style={{
                  border: `1px solid ${plan.isCurrent ? '#2f6fed' : '#1d2836'}`,
                  borderRadius: 10,
                  padding: 12,
                  background: plan.isCurrent ? '#101a2b' : '#0a0f15',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <strong style={{ fontSize: 13.5 }}>{plan.name}</strong>
                  {plan.isCurrent && <Badge tone="ok">current</Badge>}
                </div>
                <p style={{ fontSize: 11.5, color: '#8fa3b8', margin: '6px 0' }}>{plan.description}</p>
                <div style={{ fontSize: 16 }}>
                  {formatMoney(plan.monthly_price_cents, plan.currency)}
                  <span style={{ fontSize: 11, color: '#8fa3b8' }}>/month</span>
                </div>
                <div style={{ fontSize: 11, color: '#8fa3b8', marginTop: 4 }}>
                  {plan.included_voice_minutes} voice minutes · {plan.included_sms_segments} SMS segments
                </div>
                <div style={{ display: 'flex', gap: 6, marginTop: 10 }}>
                  <button
                    style={buttonStyle}
                    disabled={plan.isCurrent || busy !== null}
                    onClick={() => checkout(plan.code, 'month')}
                  >
                    {busy === `checkout:${plan.code}` ? 'Starting…' : plan.isCurrent ? 'Current' : 'Choose monthly'}
                  </button>
                  {plan.annual_price_cents !== null && (
                    <button
                      style={subtleButtonStyle}
                      disabled={plan.isCurrent || busy !== null}
                      onClick={() => checkout(plan.code, 'year')}
                    >
                      Annual
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </ConsoleCard>
    </main>
  );
}

export default BillingPage;
