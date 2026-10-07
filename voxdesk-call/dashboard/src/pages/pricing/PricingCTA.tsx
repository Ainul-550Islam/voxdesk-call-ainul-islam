import React from 'react';

export function PricingCTA() {
  return (
    <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6 lg:px-8">
      <div className="rounded-3xl border border-white/10 bg-gradient-to-r from-blue-600/20 to-violet-600/20 p-8 text-center sm:p-12">
        <h2 className="text-2xl font-bold text-white sm:text-3xl">
          Review a plan for your voice-agent workload
        </h2>
        <p className="mx-auto mt-3 max-w-xl text-sm text-white/65">
          Create a workspace to inspect its available configuration. Live calls require an eligible number, provider setup, and the necessary permissions.
        </p>
        <div className="mt-6 flex flex-wrap justify-center gap-4">
          <a href="/signup" className="rounded-xl bg-white px-6 py-3 text-xs font-semibold text-black hover:bg-white/90">
            Create Workspace
          </a>
          <a href="/contact-sales" className="rounded-xl border border-white/20 bg-white/5 px-6 py-3 text-xs font-semibold text-white hover:bg-white/10">
            Contact Sales
          </a>
        </div>
      </div>
    </section>
  );
}

export default PricingCTA;
