import React from 'react';
import { GlassCard } from '../../../components/ui/GlassCard';

const HOW_IT_WORKS_STEPS = [
  {
    step: '01',
    title: 'Inbound or Outbound Call Initiated',
    description: 'Calls arrive via SIP trunk or PSTN number (Twilio/Telnyx) or are dialed by the outbound campaign engine after DNC and calling-window checks.',
  },
  {
    step: '02',
    title: 'Real-Time Audio Streaming & ASR',
    description: 'Bidirectional WebSockets stream 16kHz/8kHz audio with low-latency voice activity detection (VAD) and streaming speech-to-text.',
  },
  {
    step: '03',
    title: 'RAG Knowledge + Tool Execution',
    description: 'The agent retrieves tenant-scoped knowledge chunks, interpolates dynamic variables, and executes mid-call CRM or calendar tools.',
  },
  {
    step: '04',
    title: 'Resolution or Warm Human Transfer',
    description: 'The call concludes with automated disposition logging or executes a warm handoff with a whisper summary to a live specialist.',
  },
];

export function VoiceAgentsHowItWorks() {
  return (
    <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-24 lg:px-8">
      <div className="max-w-3xl">
        <div className="text-xs font-semibold uppercase tracking-wider text-cyan-400">
          Architecture Flow
        </div>
        <h2 className="mt-2 text-3xl font-bold text-white sm:text-4xl">
          How Voice Agents Work in Production
        </h2>
        <p className="mt-3 text-sm leading-relaxed text-white/60">
          Deterministic state machines combined with sub-second conversational AI and enterprise telephony controls.
        </p>
      </div>
      <div className="mt-10 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
        {HOW_IT_WORKS_STEPS.map((item) => (
          <GlassCard key={item.step} className="p-6">
            <div className="font-mono text-xs font-semibold text-blue-400">STEP {item.step}</div>
            <h3 className="mt-3 text-base font-semibold text-white">{item.title}</h3>
            <p className="mt-2 text-xs leading-relaxed text-white/60">{item.description}</p>
          </GlassCard>
        ))}
      </div>
    </section>
  );
}

export default VoiceAgentsHowItWorks;
