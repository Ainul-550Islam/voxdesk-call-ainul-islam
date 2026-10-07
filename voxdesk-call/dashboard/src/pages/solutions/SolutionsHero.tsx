import React from 'react';

export function SolutionsHero() {
  return (
    <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-20 lg:px-8">
      <div className="max-w-3xl">
        <div className="inline-flex items-center gap-2 rounded-full border border-violet-500/20 bg-violet-500/10 px-3.5 py-1.5 text-xs font-medium text-violet-300">
          Enterprise Solutions • End-to-End Voice Workflows
        </div>
        <h1 className="mt-6 text-4xl font-bold tracking-tight text-white sm:text-5xl">
          Purpose-Built Voice AI Solutions for Every Department
        </h1>
        <p className="mt-4 text-base leading-relaxed text-white/65">
          Combine inbound answering, outbound campaigns, calendar booking, and CRM synchronization with deterministic compliance guardrails.
        </p>
        <div className="mt-8 flex flex-wrap gap-3">
          <a href="/use-cases" className="rounded-xl bg-white px-5 py-2.5 text-xs font-semibold text-black hover:bg-white/90">
            Browse Use Cases →
          </a>
          <a href="/industries" className="rounded-xl border border-white/15 bg-white/5 px-5 py-2.5 text-xs font-semibold text-white hover:bg-white/10">
            Explore Industries
          </a>
        </div>
      </div>
    </section>
  );
}
export default SolutionsHero;
