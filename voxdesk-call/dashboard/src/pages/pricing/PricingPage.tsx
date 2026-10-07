import React, { useCallback, useEffect, useState } from 'react';
import { PublicHeader } from '../../components/layout/PublicHeader';
import { PublicFooter } from '../../components/layout/PublicFooter';
import { fetchPublicPricingTiers, PublicSiteApiError } from '../../api/public-site';
import type { PublicPricingTier } from '../../api/types/public-widget';
import { PricingHero } from './PricingHero';
import { PricingCalculator } from './PricingCalculator';
import { PricingComparison } from './PricingComparison';
import { PricingEnterprise } from './PricingEnterprise';
import { PricingFAQ } from './PricingFAQ';
import { PricingCTA } from './PricingCTA';
import { VoiceAgentsPricing } from '../product/voice-agents/VoiceAgentsPricing';

function errorMessage(error: unknown): string {
  if (error instanceof PublicSiteApiError) return error.message;
  if (error instanceof Error && error.message.trim()) return error.message;
  return 'The public pricing request failed without a readable error message.';
}

export function PricingPage() {
  const [billingCycle, setBillingCycle] = useState<'monthly' | 'annual'>('monthly');
  const [tiers, setTiers] = useState<PublicPricingTier[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadPricing = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const publicTiers = await fetchPublicPricingTiers();
      setTiers(publicTiers);
    } catch (requestError) {
      setTiers(null);
      setError(errorMessage(requestError));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadPricing();
  }, [loadPricing]);

  const annualAvailable = Boolean(tiers?.some((tier) => tier.annual_price_cents !== null));
  const usingSeedCatalogue = Boolean(tiers?.some((tier) => tier.catalogue_source === 'SEED_DEFAULT'));
  useEffect(() => {
    if (!annualAvailable && billingCycle === 'annual') setBillingCycle('monthly');
  }, [annualAvailable, billingCycle]);

  return (
    <div className="min-h-screen bg-black text-white">
      <PublicHeader />
      <main>
        <PricingHero
          billingCycle={billingCycle}
          onBillingCycleChange={setBillingCycle}
          annualAvailable={annualAvailable}
        />

        {loading && (
          <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8" role="status">
            Loading the server-side billing plan catalogue…
          </div>
        )}

        {error && (
          <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
            <div className="rounded-2xl border border-red-500/30 bg-red-500/10 p-6" role="alert">
              <h2 className="text-lg font-semibold text-red-200">Pricing catalogue unavailable</h2>
              <p className="mt-2 text-sm text-red-100/75">{error}</p>
              <button
                type="button"
                onClick={() => void loadPricing()}
                disabled={loading}
                className="mt-4 rounded-xl bg-white px-4 py-2 text-xs font-semibold text-black disabled:opacity-60"
              >
                {loading ? 'Retrying…' : 'Retry'}
              </button>
            </div>
          </div>
        )}

        {!loading && !error && usingSeedCatalogue && (
          <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
            <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-xs leading-relaxed text-amber-100/85" role="note">
              The database returned no active plans, so these prices and allowances come from the repository&apos;s default seed catalogue. Confirm billing terms in your workspace or contract before purchase.
            </div>
          </div>
        )}

        {!loading && !error && tiers?.length === 0 && (
          <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
            <div className="rounded-2xl border border-white/10 bg-white/5 p-6" role="status">
              No active public plans are present in the billing catalogue. Contact sales for current terms.
            </div>
          </div>
        )}

        {!loading && !error && tiers && tiers.length > 0 && (
          <>
            <VoiceAgentsPricing tiers={tiers} billingCycle={billingCycle} />
            <PricingCalculator tiers={tiers} billingCycle={billingCycle} />
            <PricingComparison tiers={tiers} billingCycle={billingCycle} />
            <PricingEnterprise />
          </>
        )}

        <PricingFAQ />
        <PricingCTA />
      </main>
      <PublicFooter />
    </div>
  );
}

export default PricingPage;
