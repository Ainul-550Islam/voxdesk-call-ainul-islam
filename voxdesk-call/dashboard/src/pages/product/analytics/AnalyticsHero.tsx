import React from 'react';

export function AnalyticsHero() {
  return (
    <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-20 lg:px-8">
      <div className="max-w-3xl">
        <span className="rounded-full border border-violet-500/20 bg-violet-500/10 px-3.5 py-1.5 text-xs font-medium text-violet-300">
          Real-Time Call Telemetry & Automated QA
        </span>
        <h1 className="mt-6 text-4xl font-bold tracking-tight text-white sm:text-5xl">
          Actionable Voice Analytics & Quality Management
        </h1>
        <p className="mt-4 text-base leading-relaxed text-white/65">
          Track real call outcomes, turn-by-turn latency, conversion funnels, and automated rubric-based QA scorecards—backed 100% by real database telemetry.
        </p>
      </div>
    </section>
  );
}
export default AnalyticsHero;
