import React from 'react';

export function PricingHero({
  billingCycle = 'monthly',
  onBillingCycleChange,
  annualAvailable = false,
}: {
  billingCycle?: 'monthly' | 'annual';
  onBillingCycleChange?: (cycle: 'monthly' | 'annual') => void;
  annualAvailable?: boolean;
}) {
  return (
    <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-20 lg:px-8">
      <div className="mx-auto max-w-3xl text-center">
        <div className="inline-flex items-center gap-2 rounded-full border border-blue-500/20 bg-blue-500/10 px-3.5 py-1.5 text-xs font-medium text-blue-300">
          Price and allowance data from the server-side catalogue
        </div>
        <h1 className="mt-6 text-4xl font-bold tracking-tight text-white sm:text-5xl">
          Plans and usage for VoxDesk
        </h1>
        <p className="mt-4 text-base leading-relaxed text-white/65">
          Compare plan prices, included usage, and concurrency supplied by the server-side catalogue. Usage totals depend on actual provider and model consumption; no estimated bill or unsupported discount is shown.
        </p>
        {onBillingCycleChange && (
          <div className="mt-8 inline-flex rounded-full border border-white/10 bg-white/5 p-1" aria-label="Billing cycle">
            <button
              type="button"
              aria-pressed={billingCycle === 'monthly'}
              onClick={() => onBillingCycleChange('monthly')}
              className={`rounded-full px-4 py-1.5 text-xs font-semibold transition-colors ${billingCycle === 'monthly' ? 'bg-white text-black' : 'text-white/60 hover:text-white'}`}
            >
              Monthly
            </button>
            {annualAvailable && (
              <button
                type="button"
                aria-pressed={billingCycle === 'annual'}
                onClick={() => onBillingCycleChange('annual')}
                className={`rounded-full px-4 py-1.5 text-xs font-semibold transition-colors ${billingCycle === 'annual' ? 'bg-white text-black' : 'text-white/60 hover:text-white'}`}
              >
                Annual
              </button>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

export default PricingHero;
