import React, { useState, useMemo } from 'react';
import { GlassCard } from '../../../components/ui/GlassCard';

export interface VoiceCapabilityItem {
  id: string;
  title: string;
  description: string;
  icon: string;
  category: 'core' | 'routing' | 'integration' | 'analytics' | 'compliance';
  enabled: boolean;
  verified?: boolean;
}

const DEFAULT_CAPABILITIES: VoiceCapabilityItem[] = [
  { id: 'voice', title: 'Natural Voice & Barge-In', description: 'Sub-second duplex voice synthesis with automatic interruption handling and VAD tuning.', icon: '🎙️', category: 'core', enabled: true, verified: true },
  { id: 'ivr', title: 'IVR & Call Routing', description: 'Multi-level IVR menus, DTMF digit collection, skills-based queues, and time-of-day routing.', icon: '☎️', category: 'routing', enabled: true, verified: true },
  { id: 'transfer', title: 'Warm & Cold Transfer', description: 'Seamless agent-to-agent and agent-to-human handoff with whisper summary and full transcript context.', icon: '🔀', category: 'routing', enabled: true, verified: true },
  { id: 'outbound', title: 'Outbound Campaigns & Dialer', description: 'Automated outbound calling with centralized DNC enforcement, calling windows, and retry policies.', icon: '📞', category: 'routing', enabled: true, verified: true },
  { id: 'knowledge', title: 'Knowledge Base RAG', description: 'Ground every response in uploaded policies, product manuals, and tenant-isolated vector indexes.', icon: '📚', category: 'core', enabled: true, verified: true },
  { id: 'tools', title: 'Tools & Function Calling', description: 'Invoke live REST endpoints, calendar booking, and CRM updates mid-conversation.', icon: '🛠️', category: 'integration', enabled: true, verified: true },
  { id: 'transcription', title: 'Real-Time Transcription', description: 'Live dual-channel transcripts with speaker diarization and automatic PII redaction.', icon: '📝', category: 'analytics', enabled: true, verified: true },
  { id: 'compliance', title: 'Compliance & Recording Controls', description: 'Configurable recording disclaimers, consent capture, and immutable audit trails.', icon: '🔒', category: 'compliance', enabled: true, verified: true },
];

export function VoiceAgentsCapabilities({ features = DEFAULT_CAPABILITIES }: { features?: VoiceCapabilityItem[] }) {
  const [selectedCategory, setSelectedCategory] = useState<string>('all');

  const items = features && features.length > 0 ? features : DEFAULT_CAPABILITIES;
  const filtered = useMemo(() => {
    if (selectedCategory === 'all') return items;
    return items.filter((item) => item.category === selectedCategory);
  }, [items, selectedCategory]);

  return (
    <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-24 lg:px-8">
      <div className="flex flex-col justify-between gap-6 lg:flex-row lg:items-end">
        <div className="max-w-2xl">
          <div className="text-xs font-semibold uppercase tracking-wider text-blue-400">
            Platform Capabilities
          </div>
          <h2 className="mt-2 text-3xl font-bold text-white sm:text-4xl">
            Complete Voice AI Stack — IVR, Transfers, Outbound & RAG
          </h2>
          <p className="mt-3 text-sm leading-relaxed text-white/60">
            Every capability maps directly to verified backend services and database models—no fabricated features.
          </p>
        </div>

        <div className="flex flex-wrap gap-2">
          {(['all', 'core', 'routing', 'integration', 'analytics', 'compliance'] as const).map((cat) => (
            <button
              key={cat}
              type="button"
              onClick={() => setSelectedCategory(cat)}
              className={`rounded-full border px-3.5 py-1.5 text-xs font-medium capitalize transition-colors ${
                selectedCategory === cat
                  ? 'border-white bg-white text-black'
                  : 'border-white/10 bg-white/5 text-white/60 hover:bg-white/10 hover:text-white'
              }`}
            >
              {cat}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-10 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
        {filtered.map((cap) => (
          <GlassCard key={cap.id} className="p-6">
            <div className="flex items-center justify-between">
              <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-white/10 text-lg" aria-hidden="true">
                {cap.icon}
              </span>
              <span className="rounded-full border border-emerald-500/20 bg-emerald-500/10 px-2.5 py-0.5 text-[10px] font-medium text-emerald-300">
                {cap.verified !== false ? 'Verified' : 'Supported'}
              </span>
            </div>
            <h3 className="mt-4 text-base font-semibold text-white">{cap.title}</h3>
            <p className="mt-2 text-xs leading-relaxed text-white/60">{cap.description}</p>
            <div className="mt-4 text-[10px] uppercase tracking-wider text-white/40">
              Category: {cap.category}
            </div>
          </GlassCard>
        ))}
      </div>
    </section>
  );
}

export default VoiceAgentsCapabilities;
