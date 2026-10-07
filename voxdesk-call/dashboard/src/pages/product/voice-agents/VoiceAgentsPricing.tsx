import React, { useEffect, useState } from 'react';
import { GlassCard } from '../../../components/ui/GlassCard';
import { fetchPublicPricingTiers } from '../../../api/public-site';
import type { PublicPricingTier } from '../../../api/types/public-widget';

export type BillingCycle = 'monthly' | 'annual';

export interface VoiceAgentsPricingProps {
  tiers?: PublicPricingTier[];
  billingCycle?: BillingCycle;
}

function formatPrice(amountCents: number, currency: string): string {
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: currency.toUpperCase(),
    maximumFractionDigits: 2,
  }).format(amountCents / 100);
}

function describePricingError(error: unknown): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  return 'The public pricing request failed without a readable error message.';
}

export function VoiceAgentsPricing({
  tiers: suppliedTiers,
  billingCycle = 'monthly',
}: VoiceAgentsPricingProps = {}) {
  const [loadedTiers, setLoadedTiers] = useState<PublicPricingTier[] | null>(suppliedTiers ?? null);
  const [loading, setLoading] = useState(suppliedTiers === undefined);
  const [error, setError] = useState<string | null>(null);
  const [retryCount, setRetryCount] = useState(0);

  useEffect(() => {
    if (suppliedTiers !== undefined) {
      setLoadedTiers(suppliedTiers);
      setLoading(false);
      setError(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    void fetchPublicPricingTiers()
      .then((response) => {
        if (!cancelled) setLoadedTiers(response);
      })
      .catch((requestError: unknown) => {
        if (!cancelled) {
          setLoadedTiers(null);
          setError(describePricingError(requestError));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [suppliedTiers, retryCount]);

  const resolvedTiers = suppliedTiers ?? loadedTiers ?? [];

  if (loading) {
    return (
      <section aria-labelledby="published-plans-heading" className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-24 lg:px-8">
        <div className="max-w-3xl">
          <div className="text-xs font-semibold uppercase tracking-wider text-violet-400">
            Public billing-plan response
          </div>
          <h2 id="published-plans-heading" className="mt-2 text-3xl font-bold text-white sm:text-4xl">
            Published plans and included usage
          </h2>
          <p className="mt-3 text-sm leading-relaxed text-white/60" role="status">
            Loading the server-side billing plan catalogue…
          </p>
        </div>
      </section>
    );
  }

  if (error) {
    return (
      <section aria-labelledby="published-plans-heading" className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-24 lg:px-8">
        <div className="rounded-2xl border border-red-500/30 bg-red-500/10 p-6" role="alert">
          <h2 id="published-plans-heading" className="text-lg font-semibold text-red-200">
            Pricing catalogue unavailable
          </h2>
          <p className="mt-2 text-sm text-red-100/75">{error}</p>
          <button
            type="button"
            onClick={() => setRetryCount((count) => count + 1)}
            disabled={loading}
            className="mt-4 rounded-xl bg-white px-4 py-2 text-xs font-semibold text-black disabled:opacity-60"
          >
            Retry
          </button>
        </div>
      </section>
    );
  }

  if (resolvedTiers.length === 0) {
    return (
      <section aria-labelledby="published-plans-heading" className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-24 lg:px-8">
        <div className="max-w-3xl">
          <div className="text-xs font-semibold uppercase tracking-wider text-violet-400">
            Public billing-plan response
          </div>
          <h2 id="published-plans-heading" className="mt-2 text-3xl font-bold text-white sm:text-4xl">
            Published plans and included usage
          </h2>
          <p className="mt-4 rounded-xl border border-white/10 bg-white/5 p-5 text-sm text-white/70" role="status">
            No active public plans are present in the billing catalogue. Contact sales for current terms.
          </p>
        </div>
      </section>
    );
  }

  return (
    <section aria-labelledby="published-plans-heading" className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-24 lg:px-8">
      <div className="max-w-3xl">
        <div className="text-xs font-semibold uppercase tracking-wider text-violet-400">
          Public billing-plan response
        </div>
        <h2 id="published-plans-heading" className="mt-2 text-3xl font-bold text-white sm:text-4xl">
          Published plans and included usage
        </h2>
        <p className="mt-3 text-sm leading-relaxed text-white/60">
          Plan prices, allowances, and feature limits are read from the public billing-plan API. This page does not invent an overage estimate or imply that a provider is connected.
        </p>
      </div>

      <div className="mt-10 grid gap-6 lg:grid-cols-3">
        {resolvedTiers.map((tier) => {
          const selectedPrice = billingCycle === 'annual'
            ? tier.annual_price_cents
            : tier.monthly_price_cents;
          const cycleLabel = billingCycle === 'annual' ? '/year' : '/month';
          return (
            <GlassCard key={tier.id} className={`p-6 sm:p-8 ${tier.is_enterprise ? 'border-blue-500/40 bg-blue-500/[0.04]' : ''}`}>
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h3 className="text-lg font-semibold text-white">{tier.name}</h3>
                  <p className="mt-2 min-h-10 text-xs leading-relaxed text-white/60">
                    {tier.description || 'Plan details are supplied by the active billing catalogue.'}
                  </p>
                </div>
                {tier.is_enterprise && (
                  <span className="rounded-full bg-blue-500/20 px-2.5 py-0.5 text-[10px] font-medium text-blue-300">
                    Enterprise
                  </span>
                )}
              </div>
              <div className="mt-5 flex items-baseline gap-2">
                {selectedPrice === null ? (
                  <span className="text-xl font-semibold text-white">Not offered annually</span>
                ) : (
                  <>
                    <span className="text-3xl font-bold text-white">
                      {formatPrice(selectedPrice, tier.currency)}
                    </span>
                    <span className="text-xs text-white/50">{cycleLabel}</span>
                  </>
                )}
              </div>
              <dl className="mt-6 grid grid-cols-2 gap-x-4 gap-y-3 border-y border-white/10 py-4 text-xs">
                <div>
                  <dt className="text-white/45">Voice minutes</dt>
                  <dd className="mt-1 font-medium text-white">{tier.included_minutes.toLocaleString()} included</dd>
                </div>
                <div>
                  <dt className="text-white/45">Concurrency</dt>
                  <dd className="mt-1 font-medium text-white">{tier.max_concurrency === null ? 'Not specified' : tier.max_concurrency < 0 ? 'Unlimited' : tier.max_concurrency.toLocaleString()}</dd>
                </div>
                <div>
                  <dt className="text-white/45">SMS segments</dt>
                  <dd className="mt-1 font-medium text-white">{tier.included_sms_segments.toLocaleString()}</dd>
                </div>
                <div>
                  <dt className="text-white/45">Trial</dt>
                  <dd className="mt-1 font-medium text-white">{tier.trial_days > 0 ? `${tier.trial_days} days` : 'Not included'}</dd>
                </div>
              </dl>
              <ul className="mt-5 space-y-2.5 text-xs text-white/75">
                {tier.features.map((feature) => (
                  <li key={feature} className="flex items-start gap-2">
                    <span className="text-emerald-400" aria-hidden="true">•</span>
                    <span>{feature}</span>
                  </li>
                ))}
              </ul>
              <p className="mt-5 text-[11px] leading-relaxed text-white/45">
                {tier.overage_enabled
                  ? 'Overage is enabled for this plan; the total depends on measured usage and the configured billing terms. No estimated total is shown here.'
                  : 'Overage is not enabled for this plan. Requests beyond included limits may be restricted by subscription policy.'}
              </p>
              <a
                href={tier.cta_href}
                className="mt-7 inline-flex w-full items-center justify-center rounded-xl bg-white px-4 py-2.5 text-xs font-semibold text-black hover:bg-white/90"
              >
                {tier.cta_label}
              </a>
            </GlassCard>
          );
        })}
      </div>
    </section>
  );
}

export default VoiceAgentsPricing;
