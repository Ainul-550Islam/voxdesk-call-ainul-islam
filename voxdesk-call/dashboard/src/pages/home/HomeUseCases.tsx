import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';
import { SectionHeader } from '../../components/ui/SectionHeader';
import type { HomeEvidenceStatus, UseCase } from '../../types/home';

function statusClasses(status: HomeEvidenceStatus): string {
  return status === 'PARTIAL'
    ? 'border-amber-500/20 bg-amber-500/10 text-amber-200'
    : 'border-white/15 bg-white/5 text-white/60';
}

export function HomeUseCases({ useCases = [] }: { useCases?: UseCase[] }) {
  return (
    <section className="bg-gradient-to-b from-black via-[#0a0a1a] to-black px-4 py-20 sm:px-6 lg:px-8">
      <div className="mx-auto max-w-7xl">
        <SectionHeader
          badge="Workflow examples"
          title="Explore common voice-agent workflows"
          description="These are workflow categories, not claims that a ready-made agent, integration, or successful call is configured. API-path evidence is shown for transparency."
          align="center"
        />
        {useCases.length === 0 ? (
          <p className="mt-8 rounded-xl border border-white/10 p-5 text-center text-sm text-white/60" role="status">
            No use-case entries were returned by the public home API.
          </p>
        ) : (
          <div className="mt-12 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
            {useCases.map((useCase) => (
              <GlassCard key={useCase.id} hover>
                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-white/10 text-lg" aria-hidden="true">
                  {useCase.icon === 'headset' ? '🎧' : useCase.icon === 'calendar' ? '📅' : useCase.icon === 'filter' ? '🔍' : '📢'}
                </div>
                <h3 className="mt-4 text-base font-semibold text-white">{useCase.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-white/60">{useCase.description}</p>
                <span className={`mt-4 inline-flex rounded-full border px-2.5 py-1 text-[10px] font-semibold tracking-wide ${statusClasses(useCase.status)}`}>
                  {useCase.status} · route evidence
                </span>
                {useCase.evidence_routes.length > 0 ? (
                  <p className="mt-3 break-all font-mono text-[10px] text-white/45">{useCase.evidence_routes[0]}</p>
                ) : (
                  <p className="mt-3 text-[10px] text-white/45">No matching API route was returned by this deployment.</p>
                )}
                <a href={useCase.href} className="mt-4 inline-flex text-xs text-blue-400 hover:text-blue-300">Learn more →</a>
              </GlassCard>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

export default HomeUseCases;
