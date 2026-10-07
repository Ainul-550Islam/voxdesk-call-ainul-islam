import React from 'react';

export function HomeHero() {
  return (
    <section className="relative overflow-hidden bg-gradient-to-b from-black via-[#0a0a1a] to-black px-4 py-20 sm:px-6 lg:px-8 lg:py-32">
      <div className="pointer-events-none absolute inset-0" aria-hidden="true">
        <div className="absolute left-1/2 top-0 h-[600px] w-[800px] -translate-x-1/2 rounded-full bg-gradient-to-br from-blue-600/20 via-violet-600/20 to-cyan-600/20 blur-3xl" />
        <div className="absolute inset-0 bg-[linear-gradient(to_right,#ffffff05_1px,transparent_1px),linear-gradient(to_bottom,#ffffff05_1px,transparent_1px)] bg-[size:4rem_4rem]" />
      </div>
      <div className="relative mx-auto grid max-w-7xl items-center gap-12 lg:grid-cols-2 lg:gap-8">
        <div>
          <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-blue-500/20 bg-blue-500/10 px-4 py-1.5 text-xs font-medium text-blue-300 backdrop-blur">
            Voice-agent platform
          </div>
          <h1 className="bg-gradient-to-b from-white to-white/60 bg-clip-text text-4xl font-bold tracking-tight text-transparent sm:text-5xl lg:text-6xl">
            Build voice workflows for your business
          </h1>
          <p className="mt-6 max-w-xl text-lg leading-relaxed text-white/60">
            Configure voice agents, connect the services your team uses, and evaluate workflows before enabling live calling. Provider connectivity and production behavior depend on your deployment and account configuration.
          </p>
          <div className="mt-8 flex flex-col gap-4 sm:flex-row">
            <a href="/signup" className="inline-flex min-h-12 items-center justify-center rounded-xl bg-white px-6 py-3 text-sm font-semibold text-black hover:bg-white/90">
              Create Workspace
            </a>
            <a href="/docs" className="inline-flex min-h-12 items-center justify-center rounded-xl border border-white/20 bg-white/5 px-6 py-3 text-sm font-semibold text-white hover:bg-white/10">
              View API Docs
            </a>
          </div>
          <p className="mt-6 max-w-xl text-xs leading-relaxed text-white/40">
            Live calls require a configured provider and eligible phone number. This site does not promise a free trial, credit terms, latency target, or concurrency level unless those terms appear in your active account or contract.
          </p>
        </div>
        <div className="relative">
          <div className="rounded-3xl border border-white/10 bg-white/[0.03] p-8 shadow-2xl backdrop-blur-xl">
            <div className="flex items-center justify-between gap-4">
              <h2 className="text-sm font-medium text-white/80">Public voice demo</h2>
              <span className="rounded-full border border-white/10 bg-white/5 px-2.5 py-1 text-xs font-medium text-white/60">
                Availability shown below
              </span>
            </div>
            <p className="mt-5 text-sm leading-relaxed text-white/60">
              Request a provider-backed public session only if the deployment has configured one. The demo section reports the response from the public session API; no sample call or transcript is substituted.
            </p>
            <a href="#voice-demo" className="mt-6 inline-flex text-xs font-medium text-blue-300 hover:text-blue-200">
              Review demo availability <span className="ml-1" aria-hidden="true">↓</span>
            </a>
          </div>
        </div>
      </div>
    </section>
  );
}

export default HomeHero;
