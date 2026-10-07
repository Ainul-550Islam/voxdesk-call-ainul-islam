import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';
import { SectionHeader } from '../../components/ui/SectionHeader';
import type { AnalyticsSummary } from '../../types/home';

export function HomeAnalyticsPreview({ analytics }: { analytics?: AnalyticsSummary | null }) {
  const isNotConfigured = analytics?.status === 'not_configured';
  const isError = analytics?.status === 'error';
  const isEmpty = !analytics || analytics.status === 'empty';
  const isAvailable = analytics?.status === 'ok';

  return (
    <section className="bg-gradient-to-b from-black via-[#0a0a1a] to-black px-4 py-20 sm:px-6 lg:px-8">
      <div className="mx-auto max-w-7xl">
        <SectionHeader
          badge="Analytics"
          title="Call metrics from the authenticated data path"
          description="Values appear only when the public analytics API returns them. Missing values stay unavailable; this preview does not synthesize performance metrics."
          align="center"
        />
        <div className="mt-12">
          {isNotConfigured && (
            <GlassCard className="py-16 text-center">
              <div className="mx-auto max-w-md">
                <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full border border-amber-500/20 bg-amber-500/10 text-amber-300" aria-hidden="true">!</div>
                <h3 className="mt-4 text-base font-medium text-white">Analytics are not configured</h3>
                <p className="mt-2 text-sm text-white/60">{analytics?.message || 'The analytics API did not report configuration.'}</p>
              </div>
            </GlassCard>
          )}
          {isError && (
            <GlassCard className="py-16 text-center" role="alert">
              <div className="mx-auto max-w-md">
                <h3 className="text-base font-medium text-red-200">Analytics are temporarily unavailable</h3>
                <p className="mt-2 text-sm text-white/60">{analytics?.message || 'The analytics request failed.'}</p>
              </div>
            </GlassCard>
          )}
          {isEmpty && (
            <GlassCard className="py-16 text-center">
              <div className="mx-auto max-w-md">
                <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full border border-white/20 bg-white/10 text-white/60" aria-hidden="true">📊</div>
                <h3 className="mt-4 text-base font-medium text-white">No call data yet</h3>
                <p className="mt-2 text-sm text-white/60">{analytics?.message || 'No analytics records were returned.'}</p>
              </div>
            </GlassCard>
          )}
          {isAvailable && analytics && (
            <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
              <GlassCard>
                <div className="text-xs text-white/50">Total Calls</div>
                <div className="mt-2 text-2xl font-bold text-white">{analytics.calls === null ? '—' : analytics.calls.toLocaleString()}</div>
                <div className="mt-1 text-xs text-white/40">Returned by public analytics API</div>
              </GlassCard>
              <GlassCard>
                <div className="text-xs text-white/50">Successful Calls</div>
                <div className="mt-2 text-2xl font-bold text-white">{analytics.successful_calls === null ? '—' : analytics.successful_calls.toLocaleString()}</div>
                <div className="mt-1 text-xs text-emerald-400">Returned successful-call count</div>
              </GlassCard>
              <GlassCard>
                <div className="text-xs text-white/50">Average Duration</div>
                <div className="mt-2 text-2xl font-bold text-white">
                  {analytics.average_duration_seconds === null ? '—' : `${Math.round(analytics.average_duration_seconds)}s`}
                </div>
                <div className="mt-1 text-xs text-white/40">Unavailable when the API provides no value</div>
              </GlassCard>
              <GlassCard>
                <div className="text-xs text-white/50">Average Latency</div>
                <div className="mt-2 text-2xl font-bold text-white">
                  {analytics.average_latency_ms === null ? '—' : `${analytics.average_latency_ms}ms`}
                </div>
                <div className="mt-1 text-xs text-white/40">No default latency is substituted</div>
              </GlassCard>
            </div>
          )}
        </div>
        <p className="mt-8 text-center text-xs text-white/35">
          This public preview does not expose tenant-private analytics. Sign in to view workspace-scoped metrics.
        </p>
      </div>
    </section>
  );
}

export default HomeAnalyticsPreview;
