import React from 'react';
import { VoiceOrb } from '../../components/voice/VoiceOrb';
import { Waveform } from '../../components/voice/Waveform';
import { Button } from '../../components/ui/Button';
import { GlassCard } from '../../components/ui/GlassCard';
import { SectionHeader } from '../../components/ui/SectionHeader';
import { useVoiceDemo } from '../../hooks/useVoiceDemo';

export function HomeVoiceDemo() {
  const { state, start, stop, loading, error, isConfigured } = useVoiceDemo();
  const isActive = state === 'LISTENING' || state === 'PROCESSING' || state === 'SPEAKING';

  return (
    <section id="voice-demo" aria-labelledby="voice-demo-heading" className="bg-black px-4 py-20 sm:px-6 lg:px-8">
      <div className="mx-auto max-w-7xl">
        <div className="grid items-center gap-12 lg:grid-cols-2">
          <div>
            <SectionHeader
              badge="Voice demo"
              title="Provider-backed demo availability"
              description="A live audio session is created only by the backend after a provider-backed demo is configured. Until then, a request returns an explicit error; no synthetic transcript, audio, or call result is shown."
            />
            <div className="mt-8 rounded-xl border border-amber-500/20 bg-amber-500/10 p-4">
              <h3 className="text-sm font-medium text-amber-100">Current public demo state</h3>
              <p className="mt-2 text-xs leading-relaxed text-amber-100/70">
                {isConfigured
                  ? 'The server reported a configured provider-backed session.'
                  : 'No provider-backed session has been returned by the public demo API.'}
              </p>
            </div>
          </div>
          <GlassCard padding="lg">
            <div className="mb-6 flex items-center justify-between gap-4">
              <h3 id="voice-demo-heading" className="text-sm font-medium text-white">Public voice session</h3>
              <span className="rounded-full bg-white/10 px-2.5 py-1 text-xs text-white/60">{state}</span>
            </div>
            <div className="flex flex-col items-center gap-6">
              <VoiceOrb state={state} size="lg" />
              <Waveform state={state} bars={32} className="w-full" />
              <div className="flex flex-wrap justify-center gap-3">
                <Button variant="primary" loading={loading} disabled={loading} onClick={isActive ? stop : start}>
                  {isActive ? 'Stop Session' : 'Request Demo Session'}
                </Button>
                <a href="/contact-sales" className="inline-flex min-h-10 items-center rounded-xl border border-white/20 px-4 py-2 text-xs font-medium text-white hover:bg-white/10">
                  Discuss a configured demo
                </a>
              </div>
              {!isConfigured && (
                <p className="max-w-md text-center text-xs leading-relaxed text-white/45">
                  The session request may fail while the provider-backed public demo is not configured. The resulting API error is shown below.
                </p>
              )}
              {error && (
                <div className="w-full rounded-xl border border-red-500/20 bg-red-500/10 p-3 text-center text-xs text-red-300" role="alert">
                  {error}
                </div>
              )}
            </div>
          </GlassCard>
        </div>
      </div>
    </section>
  );
}

export default HomeVoiceDemo;
