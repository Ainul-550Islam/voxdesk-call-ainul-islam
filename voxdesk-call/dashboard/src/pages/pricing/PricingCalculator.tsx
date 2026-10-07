import React, { useMemo, useState } from 'react';
import { GlassCard } from '../../components/ui/GlassCard';
import type { PublicPricingTier } from '../../api/types/public-widget';
import type { BillingCycle } from '../product/voice-agents/VoiceAgentsPricing';

function formatPrice(amountCents: number, currency: string): string {
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: currency.toUpperCase(),
    maximumFractionDigits: 2,
  }).format(amountCents / 100);
}

export function PricingCalculator({
  tiers,
  billingCycle,
}: {
  tiers: PublicPricingTier[];
  billingCycle: BillingCycle;
}) {
  const [minutesPerPeriod, setMinutesPerPeriod] = useState(500);
  const [concurrentCalls, setConcurrentCalls] = useState(1);

  const eligiblePlans = useMemo(() => tiers.filter((tier) => {
    const concurrencyFits = tier.max_concurrency !== null && (tier.max_concurrency < 0 || tier.max_concurrency >= concurrentCalls);
    const billingCycleAvailable = billingCycle === 'monthly' || tier.annual_price_cents !== null;
    return concurrencyFits && tier.included_minutes >= minutesPerPeriod && billingCycleAvailable;
  }), [billingCycle, concurrentCalls, minutesPerPeriod, tiers]);

  const eligibleCurrencies = useMemo(
    () => new Set(eligiblePlans.map((tier) => tier.currency.toUpperCase())),
    [eligiblePlans],
  );

  const recommended = useMemo(() => {
    if (eligibleCurrencies.size !== 1) return null;
    return [...eligiblePlans].sort((left, right) => {
      const leftPrice = billingCycle === 'annual'
        ? left.annual_price_cents ?? Number.MAX_SAFE_INTEGER
        : left.monthly_price_cents;
      const rightPrice = billingCycle === 'annual'
        ? right.annual_price_cents ?? Number.MAX_SAFE_INTEGER
        : right.monthly_price_cents;
      return leftPrice - rightPrice;
    })[0] ?? null;
  }, [billingCycle, eligibleCurrencies, eligiblePlans]);

  function updateMinutes(value: string) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) setMinutesPerPeriod(Math.max(0, Math.floor(parsed)));
  }

  function updateConcurrency(value: string) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) setConcurrentCalls(Math.max(1, Math.floor(parsed)));
  }

  const selectedPrice = recommended
    ? billingCycle === 'annual' ? recommended.annual_price_cents : recommended.monthly_price_cents
    : null;

  return (
    <section aria-labelledby="plan-fit-heading" className="mx-auto max-w-7xl px-4 py-12 sm:px-6 lg:px-8">
      <GlassCard className="p-6 sm:p-8">
        <div className="grid gap-8 lg:grid-cols-12 lg:items-center">
          <div className="lg:col-span-7">
            <div className="text-xs font-semibold uppercase tracking-wider text-blue-400">
              Allowance fit — not a bill quote
            </div>
            <h2 id="plan-fit-heading" className="mt-2 text-2xl font-bold text-white sm:text-3xl">
              Find a plan with enough included capacity
            </h2>
            <p className="mt-2 text-xs leading-relaxed text-white/60">
              Choose expected usage and concurrency. The result uses limits from the active plan API. It does not estimate provider, LLM, TTS, tax, or overage charges.
            </p>

            <div className="mt-6 grid gap-5 sm:grid-cols-2">
              <div>
                <label htmlFor="calc-minutes" className="block text-xs font-medium text-white">
                  Expected voice minutes per billing period
                </label>
                <input
                  id="calc-minutes"
                  type="number"
                  min={0}
                  step={1}
                  inputMode="numeric"
                  value={minutesPerPeriod}
                  onChange={(event) => updateMinutes(event.target.value)}
                  className="mt-2 w-full rounded-xl border border-white/15 bg-black/40 px-3 py-2 text-sm text-white focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-400/30"
                />
              </div>
              <div>
                <label htmlFor="calc-concurrency" className="block text-xs font-medium text-white">
                  Peak concurrent calls
                </label>
                <input
                  id="calc-concurrency"
                  type="number"
                  min={1}
                  step={1}
                  inputMode="numeric"
                  value={concurrentCalls}
                  onChange={(event) => updateConcurrency(event.target.value)}
                  className="mt-2 w-full rounded-xl border border-white/15 bg-black/40 px-3 py-2 text-sm text-white focus:border-violet-400 focus:outline-none focus:ring-2 focus:ring-violet-400/30"
                />
              </div>
            </div>
          </div>

          <div className="rounded-2xl border border-white/10 bg-black/50 p-6 lg:col-span-5" aria-live="polite">
            {recommended && selectedPrice !== null ? (
              <>
                <div className="text-xs uppercase tracking-wider text-white/40">Lowest published plan meeting these allowances</div>
                <div className="mt-2 text-xl font-bold text-white">{recommended.name}</div>
                <div className="mt-2 text-3xl font-bold text-white">
                  {formatPrice(selectedPrice, recommended.currency)}
                  <span className="ml-2 text-xs font-normal text-white/50">
                    {billingCycle === 'annual' ? '/year base price' : '/month base price'}
                  </span>
                </div>
                <p className="mt-4 text-xs leading-relaxed text-white/60">
                  Includes up to {recommended.included_minutes.toLocaleString()} voice minutes and {recommended.max_concurrency === null ? 'no published concurrency limit' : recommended.max_concurrency < 0 ? 'unlimited' : recommended.max_concurrency.toLocaleString()} concurrent calls. This is the published plan base price only, not a total usage quote.
                </p>
                <a href={recommended.cta_href} className="mt-5 inline-flex rounded-xl bg-white px-4 py-2.5 text-xs font-semibold text-black hover:bg-white/90">
                  {recommended.cta_label}
                </a>
              </>
            ) : eligiblePlans.length > 0 ? (
              <>
                <div className="text-xs uppercase tracking-wider text-white/40">Eligible plans use different currencies</div>
                <p className="mt-3 text-sm leading-relaxed text-white/70">
                  {eligiblePlans.map((tier) => tier.name).join(', ')} meet the selected allowances, but the catalogue uses more than one currency. A single lowest-price recommendation would be misleading; compare the published plan cards above.
                </p>
              </>
            ) : (
              <>
                <div className="text-xs uppercase tracking-wider text-white/40">No published plan meets this selection</div>
                <p className="mt-3 text-sm leading-relaxed text-white/70">
                  The active catalogue has no plan with both the selected included-minute and concurrency allowances for this billing cycle. Contact sales to review requirements; this page will not invent a quote.
                </p>
                <a href="/contact-sales" className="mt-5 inline-flex rounded-xl border border-white/20 bg-white/5 px-4 py-2.5 text-xs font-semibold text-white hover:bg-white/10">
                  Request a scoped quote
                </a>
              </>
            )}
          </div>
        </div>
      </GlassCard>
    </section>
  );
}

export default PricingCalculator;
