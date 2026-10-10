import React from 'react';

interface Props {
  total?: number;
  className?: string;
}

export function UseCasesHero({ total, className = '' }: Props) {
  return (
    <div className={`relative overflow-hidden rounded-[32px] border border-white/10 bg-gradient-to-br from-white/[0.08] via-white/[0.03] to-transparent p-8 sm:p-12 ${className}`}>
      <div className="absolute inset-0 bg-gradient-to-br from-blue-500/10 via-violet-500/10 to-cyan-500/10 opacity-60" aria-hidden="true" />
      <div className="absolute -top-24 -right-24 h-96 w-96 rounded-full bg-gradient-to-br from-blue-500/20 to-violet-500/20 blur-3xl" aria-hidden="true" />
      <div className="absolute -bottom-24 -left-24 h-96 w-96 rounded-full bg-gradient-to-br from-cyan-500/10 to-blue-500/10 blur-3xl" aria-hidden="true" />
      <div className="relative grid gap-8 lg:grid-cols-2 lg:items-center">
        <div>
          <div className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1 text-[11px] text-white/60">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" aria-hidden="true" />
            Real backend — no fake data
          </div>
          <h1 className="mt-6 text-4xl font-bold tracking-tight text-white sm:text-5xl lg:text-[48px] leading-[1.1]">
            Build voice AI for the work that matters
          </h1>
          <p className="mt-4 text-[15px] leading-relaxed text-white/60 max-w-xl">
            Production voice agents for receptionists, call centers, industry workflows, assistants, and sales ops. Real backend, verified integrations, no fake metrics.
          </p>
          <div className="mt-6 flex flex-wrap gap-3">
            <a href="#use-cases-grid" className="rounded-xl bg-white px-5 py-2.5 text-sm font-medium text-black hover:bg-white/90">Explore use cases</a>
            <a href="/docs/use-cases" className="rounded-xl border border-white/15 bg-white/5 px-5 py-2.5 text-sm font-medium text-white hover:bg-white/10">View docs</a>
          </div>
          {total !== undefined && <div className="mt-6 text-xs text-white/40" aria-live="polite">{total} production use cases — real data only</div>}
        </div>
        <div className="relative hidden lg:block">
          <div className="relative mx-auto w-full max-w-sm">
            <div className="rounded-[24px] border border-white/10 bg-black/50 p-4 backdrop-blur">
              <div className="flex items-center gap-2">
                <div className="h-2.5 w-2.5 rounded-full bg-red-400" />
                <div className="h-2.5 w-2.5 rounded-full bg-amber-400" />
                <div className="h-2.5 w-2.5 rounded-full bg-emerald-400" />
                <div className="ml-auto text-[10px] text-white/30">Example conversation</div>
              </div>
              <div className="mt-4 space-y-3">
                <div className="rounded-xl bg-white/5 p-3 text-xs text-white/70">Inbound → Voice Agent → Knowledge/Tools → Business Action → Human Handoff</div>
                <div className="flex gap-2">
                  <div className="h-8 w-8 rounded-full bg-blue-500/20 flex items-center justify-center text-xs">C</div>
                  <div className="rounded-2xl rounded-bl-sm bg-white/10 px-3 py-2 text-xs text-white/80 max-w-[80%]">Hi, I need to book an appointment for tomorrow</div>
                </div>
                <div className="flex gap-2 justify-end">
                  <div className="rounded-2xl rounded-br-sm bg-white px-3 py-2 text-xs text-black max-w-[80%]">Of course! I can help you book. What time works best?</div>
                  <div className="h-8 w-8 rounded-full bg-white flex items-center justify-center text-xs text-black">AI</div>
                </div>
              </div>
            </div>
            <div className="absolute -bottom-6 -right-6 rounded-xl border border-white/10 bg-white/[0.05] px-3 py-2 text-[11px] text-white/60 backdrop-blur">Verified integrations only</div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default UseCasesHero;
