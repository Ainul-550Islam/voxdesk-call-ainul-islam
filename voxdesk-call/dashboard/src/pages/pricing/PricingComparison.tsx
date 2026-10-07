import React from 'react';
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

export function PricingComparison({
  tiers,
  billingCycle,
}: {
  tiers: PublicPricingTier[];
  billingCycle: BillingCycle;
}) {
  return (
    <section aria-labelledby="plan-comparison-heading" className="mx-auto max-w-7xl px-4 py-12 sm:px-6 lg:px-8">
      <h2 id="plan-comparison-heading" className="text-2xl font-bold text-white sm:text-3xl">
        Plan and usage allowance comparison
      </h2>
      <p className="mt-2 text-xs leading-relaxed text-white/60">
        Every value below is taken from the public billing-plan response. When the database has no active plans, the server labels its default seed catalogue above. Feature availability is not a provider-connectivity or production-readiness claim.
      </p>
      <GlassCard className="mt-6 overflow-x-auto p-0">
        <table className="pricing-comparison-table w-full min-w-[760px] text-xs">
          <caption className="sr-only">Current published plan prices and allowances</caption>
          <thead>
            <tr className="bg-white/5 text-white">
              <th scope="col" className="p-4 text-left">Plan term</th>
              {tiers.map((tier) => (
                <th scope="col" key={tier.id} className="p-4 text-left">
                  {tier.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr className="border-t border-white/10">
              <th scope="row" className="p-4 text-left font-medium text-white">Base price</th>
              {tiers.map((tier) => {
                const amount = billingCycle === 'annual'
                  ? tier.annual_price_cents
                  : tier.monthly_price_cents;
                return (
                  <td key={tier.id} className="p-4 text-white/75">
                    {amount === null ? 'Not offered annually' : `${formatPrice(amount, tier.currency)} ${billingCycle === 'annual' ? '/ year' : '/ month'}`}
                  </td>
                );
              })}
            </tr>
            <tr className="border-t border-white/10">
              <th scope="row" className="p-4 text-left font-medium text-white">Included voice minutes</th>
              {tiers.map((tier) => <td key={tier.id} className="p-4 text-white/75">{tier.included_minutes.toLocaleString()}</td>)}
            </tr>
            <tr className="border-t border-white/10">
              <th scope="row" className="p-4 text-left font-medium text-white">Maximum concurrency</th>
              {tiers.map((tier) => <td key={tier.id} className="p-4 text-white/75">{tier.max_concurrency === null ? 'Not specified' : tier.max_concurrency < 0 ? 'Unlimited' : tier.max_concurrency.toLocaleString()}</td>)}
            </tr>
            <tr className="border-t border-white/10">
              <th scope="row" className="p-4 text-left font-medium text-white">Included SMS segments</th>
              {tiers.map((tier) => <td key={tier.id} className="p-4 text-white/75">{tier.included_sms_segments.toLocaleString()}</td>)}
            </tr>
            <tr className="border-t border-white/10">
              <th scope="row" className="p-4 text-left font-medium text-white">Included LLM tokens</th>
              {tiers.map((tier) => <td key={tier.id} className="p-4 text-white/75">{tier.included_llm_tokens.toLocaleString()}</td>)}
            </tr>
            <tr className="border-t border-white/10">
              <th scope="row" className="p-4 text-left font-medium text-white">Included TTS characters</th>
              {tiers.map((tier) => <td key={tier.id} className="p-4 text-white/75">{tier.included_tts_characters.toLocaleString()}</td>)}
            </tr>
            <tr className="border-t border-white/10">
              <th scope="row" className="p-4 text-left font-medium text-white">Overage policy</th>
              {tiers.map((tier) => <td key={tier.id} className="p-4 text-white/75">{tier.overage_enabled ? 'Enabled; rate not displayed here' : 'Disabled'}</td>)}
            </tr>
            <tr className="border-t border-white/10">
              <th scope="row" className="p-4 text-left font-medium text-white">Catalogue description</th>
              {tiers.map((tier) => <td key={tier.id} className="p-4 text-white/75">{tier.description || 'No description supplied.'}</td>)}
            </tr>
          </tbody>
        </table>
      </GlassCard>
    </section>
  );
}

export default PricingComparison;
