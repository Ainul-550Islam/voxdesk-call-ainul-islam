import React from 'react';

export function HomeFinalCTA() {
  return (
    <section className="relative overflow-hidden bg-gradient-to-b from-black via-[#0a0a1a] to-black px-4 py-20 sm:px-6 lg:px-8">
      <div className="pointer-events-none absolute inset-0" aria-hidden="true">
        <div className="absolute left-1/2 top-1/2 h-[600px] w-[800px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-gradient-to-br from-blue-600/20 via-violet-600/20 to-cyan-600/20 blur-3xl" />
      </div>
      <div className="relative mx-auto max-w-4xl text-center">
        <h2 className="bg-gradient-to-b from-white to-white/60 bg-clip-text text-3xl font-bold tracking-tight text-transparent sm:text-4xl lg:text-5xl">
          Explore VoxDesk for your workflow
        </h2>
        <p className="mx-auto mt-6 max-w-2xl text-base leading-relaxed text-white/60 sm:text-lg">
          Review the configured workspace capabilities, published billing catalogue, and integration requirements before enabling production traffic.
        </p>
        <div className="mt-10 flex flex-col justify-center gap-4 sm:flex-row">
          <a href="/signup" className="inline-flex min-h-12 items-center justify-center rounded-xl bg-white px-6 py-3 text-sm font-semibold text-black hover:bg-white/90">
            Create Workspace
          </a>
          <a href="/contact-sales" className="inline-flex min-h-12 items-center justify-center rounded-xl border border-white/20 px-6 py-3 text-sm font-semibold text-white hover:bg-white/10">
            Contact Sales
          </a>
          <a href="/docs" className="inline-flex min-h-12 items-center justify-center rounded-xl border border-white/20 px-6 py-3 text-sm font-semibold text-white hover:bg-white/10">
            Developer Docs
          </a>
        </div>
        <p className="mt-6 text-xs text-white/40">
          No trial, connectivity, response-time, or production-readiness promise is made by this page.
        </p>
      </div>
    </section>
  );
}

export default HomeFinalCTA;
