import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';
import { SectionHeader } from '../../components/ui/SectionHeader';
import type { DeveloperFeature, HomeEvidenceStatus } from '../../types/home';

function statusClasses(status: HomeEvidenceStatus): string {
  return status === 'PARTIAL'
    ? 'border-amber-500/20 bg-amber-500/10 text-amber-200'
    : 'border-white/15 bg-white/5 text-white/60';
}

export function HomeDeveloper({ features = [] }: { features?: DeveloperFeature[] }) {
  return (
    <section className="bg-black px-4 py-20 sm:px-6 lg:px-8">
      <div className="mx-auto max-w-7xl">
        <div className="grid items-start gap-12 lg:grid-cols-2">
          <div>
            <SectionHeader
              badge="Developers"
              title="Inspect the API surface"
              description="Registered paths help locate the implementation surface. They do not prove authorization, successful persistence, provider connectivity, or end-to-end behavior."
            />
            <div className="mt-8 space-y-4">
              {features.length === 0 ? (
                <p className="rounded-xl border border-white/10 p-5 text-sm text-white/60" role="status">
                  No developer-feature entries were returned by the public home API.
                </p>
              ) : features.map((feature) => (
                <div key={feature.id} className="rounded-xl border border-white/10 bg-white/[0.02] p-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <h3 className="text-sm font-medium text-white">{feature.title}</h3>
                    <span className={`inline-flex rounded-full border px-2.5 py-1 text-[10px] font-semibold tracking-wide ${statusClasses(feature.status)}`}>
                      {feature.status} · route evidence
                    </span>
                  </div>
                  <p className="mt-2 text-xs leading-relaxed text-white/60">{feature.description}</p>
                  {feature.evidence_routes.length > 0 ? (
                    <ul className="mt-3 space-y-1 text-[10px] text-white/45" aria-label={`Registered route evidence for ${feature.title}`}>
                      {feature.evidence_routes.slice(0, 3).map((route) => <li key={route} className="break-all font-mono">{route}</li>)}
                    </ul>
                  ) : (
                    <p className="mt-3 text-[10px] text-white/45">No matching API route was returned by this deployment.</p>
                  )}
                </div>
              ))}
            </div>
            <div className="mt-8 flex flex-wrap gap-3">
              <a href="/docs" className="inline-flex h-10 items-center rounded-xl bg-white px-5 text-sm font-medium text-black hover:bg-white/90">Read API Docs</a>
              <a href="/developers/sdk" className="inline-flex h-10 items-center rounded-xl border border-white/20 px-5 text-sm font-medium text-white hover:bg-white/10">SDK availability</a>
            </div>
          </div>
          <GlassCard padding="lg">
            <h3 className="text-sm font-semibold text-white">Public demo endpoint status</h3>
            <p className="mt-2 text-xs leading-relaxed text-white/60">
              A provider-backed public voice session is not configured. The session endpoint fails closed instead of returning a fabricated session ID or success payload.
            </p>
            <pre className="mt-5 overflow-x-auto rounded-xl border border-white/10 bg-black/40 p-4 text-xs leading-relaxed text-white/60"><code>{'POST /api/v1/public/voice-demo/session\nExpected result until configured: HTTP 503\nNo session is created.'}</code></pre>
            <p className="mt-4 text-[10px] text-white/40">
              This is an endpoint contract description, not a captured request or test result.
            </p>
          </GlassCard>
        </div>
      </div>
    </section>
  );
}

export default HomeDeveloper;
