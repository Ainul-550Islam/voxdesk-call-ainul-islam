import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';
import { SectionHeader } from '../../components/ui/SectionHeader';
import type { Capability, HomeEvidenceStatus } from '../../types/home';

function statusClasses(status: HomeEvidenceStatus): string {
  return status === 'PARTIAL'
    ? 'border-amber-500/20 bg-amber-500/10 text-amber-200'
    : 'border-white/15 bg-white/5 text-white/60';
}

export function HomeCapabilities({ capabilities = [] }: { capabilities?: Capability[] }) {
  return (
    <section className="bg-black px-4 py-20 sm:px-6 lg:px-8">
      <div className="mx-auto max-w-7xl">
        <SectionHeader
          badge="Product surfaces"
          title="Explore the voice-agent platform"
          description="Items below are matched to registered API paths. Route registration is evidence of an API surface—not proof of provider configuration, complete workflows, or production readiness."
        />
        {capabilities.length === 0 ? (
          <p className="mt-8 rounded-xl border border-white/10 p-5 text-sm text-white/60" role="status">
            No capability entries were returned by the public home API.
          </p>
        ) : (
          <div className="mt-12 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
            {capabilities.map((capability) => (
              <GlassCard key={capability.id} hover className="group">
                <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-white/10 bg-gradient-to-br from-blue-600/20 to-violet-600/20 transition-colors group-hover:from-blue-600/30 group-hover:to-violet-600/30">
                  <span className="text-lg" aria-hidden="true">
                    {capability.icon === 'bot' ? '🤖' : capability.icon === 'phone' ? '📞' : capability.icon === 'eye' ? '👁️' : '⚡'}
                  </span>
                </div>
                <h3 className="mt-4 text-base font-semibold text-white">{capability.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-white/60">{capability.description}</p>
                <span className={`mt-4 inline-flex rounded-full border px-2.5 py-1 text-[10px] font-semibold tracking-wide ${statusClasses(capability.status)}`}>
                  {capability.status} · route evidence
                </span>
                {capability.evidence_routes.length > 0 ? (
                  <ul className="mt-3 space-y-1 text-[10px] text-white/45" aria-label={`Registered route evidence for ${capability.title}`}>
                    {capability.evidence_routes.slice(0, 2).map((route) => <li key={route} className="break-all font-mono">{route}</li>)}
                  </ul>
                ) : (
                  <p className="mt-3 text-[10px] text-white/45">No matching API route was returned by this deployment.</p>
                )}
                <a href={capability.href} className="mt-4 inline-flex items-center text-xs font-medium text-blue-400 hover:text-blue-300">
                  Explore <span className="ml-1" aria-hidden="true">→</span>
                </a>
              </GlassCard>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

export default HomeCapabilities;
