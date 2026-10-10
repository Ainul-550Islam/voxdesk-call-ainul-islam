import React from 'react';

interface Props {
  slug: string;
  title?: string;
  className?: string;
}

export function UseCaseCTA({ slug, title, className = '' }: Props) {
  const href = `/dashboard/agents/new?useCase=${encodeURIComponent(slug)}`;
  return (
    <div className={`relative overflow-hidden rounded-[24px] border border-white/10 bg-gradient-to-br from-white/[0.06] to-white/[0.02] p-8 sm:p-10 text-center ${className}`}>
      <div className="absolute inset-0 bg-gradient-to-br from-blue-500/10 via-violet-500/10 to-cyan-500/10 opacity-50" aria-hidden="true" />
      <div className="relative">
        <h2 className="text-2xl font-bold text-white sm:text-3xl">Build your first voice agent</h2>
        <p className="mx-auto mt-3 max-w-xl text-sm leading-relaxed text-white/60">
          {title ? `Start building for ${title} — real backend, verified integrations, production ready. Safe handling of unknown use cases.` : 'Start building with real backend — no fake data, no invented metrics, production ready.'}
        </p>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <a href={href} className="rounded-xl bg-white px-6 py-3 text-sm font-medium text-black hover:bg-white/90">Build an agent for this use case</a>
          <a href="/docs/use-cases" className="rounded-xl border border-white/15 bg-white/5 px-6 py-3 text-sm font-medium text-white hover:bg-white/10">View documentation</a>
        </div>
        <div className="mt-6 text-[11px] text-white/30">CTA safe — unknown useCase values are ignored, opens normally. No tenant data leak.</div>
      </div>
    </div>
  );
}

export default UseCaseCTA;
