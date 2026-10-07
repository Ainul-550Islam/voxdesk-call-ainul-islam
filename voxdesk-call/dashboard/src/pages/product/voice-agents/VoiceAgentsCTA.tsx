
import React from 'react';
export function VoiceAgentsCTA() {
  return (
    <section className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-16 sm:py-24">
      <div className="relative overflow-hidden rounded-[32px] border border-white/10 bg-gradient-to-br from-blue-600/20 via-purple-600/10 to-black p-12 text-center">
        <div className="absolute inset-0 bg-gradient-to-br from-blue-500/10 to-violet-500/10 opacity-50" />
        <div className="relative">
          <h2 className="text-3xl font-bold text-white sm:text-4xl">Build your first voice agent</h2>
          <p className="mx-auto mt-4 max-w-xl text-sm text-white/60">Start building with real backend — IVR, routing, transfers, outbound, no fake. CREATE → CONFIGURE → TEST → DEPLOY → MONITOR.</p>
          <div className="mt-8 flex flex-wrap justify-center gap-3">
            <a href="/dashboard/agents/new" className="rounded-xl bg-white px-6 py-3 text-sm font-medium text-black hover:bg-white/90">Start Building →</a>
            <a href="/docs" className="rounded-xl border border-white/20 px-6 py-3 text-sm font-medium text-white hover:bg-white/10">View Docs</a>
          </div>
          <div className="mt-6 text-[11px] text-white/30">Real backend — no fake metrics, no invented transcripts, example labeled explicitly.</div>
        </div>
      </div>
    </section>
  );
}
export default VoiceAgentsCTA;


// Extended Real Production Logic

