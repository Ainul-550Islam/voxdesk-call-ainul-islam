import React from 'react';

export function DevelopersHero() {
  return (
    <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-20 lg:px-8">
      <div className="max-w-3xl">
        <p className="inline-flex items-center rounded-full border border-blue-500/20 bg-blue-500/10 px-3.5 py-1.5 text-xs font-medium text-blue-300">
          Source-backed developer information
        </p>
        <h1 className="mt-6 text-4xl font-bold tracking-tight text-white sm:text-5xl">
          Build against the API surfaces that exist
        </h1>
        <p className="mt-4 text-base leading-relaxed text-white/65">
          This portal lists representative routes registered in the VoxDesk backend and labels their access boundaries. Route registration is not a guarantee that a provider is configured, an operation will succeed, or an endpoint is available in every deployment.
        </p>
        <div className="mt-8 flex flex-wrap gap-3">
          <a href="/docs" className="rounded-xl bg-white px-5 py-2.5 text-xs font-semibold text-black hover:bg-white/90">
            Read the API notes
          </a>
          <a href="/dashboard/final-parity" className="rounded-xl border border-white/15 bg-white/5 px-5 py-2.5 text-xs font-semibold text-white hover:bg-white/10">
            Inspect authenticated route evidence
          </a>
        </div>
        <p className="mt-6 text-xs leading-relaxed text-white/40">
          No public SDK package, generated OpenAPI document, universal webhook contract, or platform release changelog is advertised here unless one is published and verified for this deployment.
        </p>
      </div>
    </section>
  );
}

export default DevelopersHero;
