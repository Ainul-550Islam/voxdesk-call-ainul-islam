import React from 'react';
import { GlassCard } from '../../../components/ui/GlassCard';
import { Button } from '../../../components/ui/Button';

export interface VoiceAgentsHeroProps {
  onSeeHowItWorks?: () => void;
  stats?: {
    totalAgents?: number;
    activeCalls?: number;
    avgLatencyMs?: number;
    uptime?: string;
  } | null;
}

const HERO_STAGES = [
  { step: '01', code: 'CREATE', label: 'Define prompt, voice & persona' },
  { step: '02', code: 'CONFIGURE', label: 'Attach RAG knowledge & CRM tools' },
  { step: '03', code: 'TEST', label: 'Run deterministic scenario suites' },
  { step: '04', code: 'DEPLOY', label: 'Bind E.164 numbers & SIP trunks' },
  { step: '05', code: 'MONITOR', label: 'Live listen, whisper, barge & QA' },
];

export function VoiceAgentsHero({ onSeeHowItWorks, stats }: VoiceAgentsHeroProps) {
  return (
    <section className="relative overflow-hidden border-b border-white/10 bg-gradient-to-b from-black via-[#080816] to-black px-4 py-20 sm:px-6 lg:px-8 lg:py-28">
      <div className="mx-auto max-w-7xl">
        <div className="grid gap-12 lg:grid-cols-12 lg:items-center">
          <div className="lg:col-span-7">
            <div className="inline-flex items-center gap-2 rounded-full border border-blue-500/20 bg-blue-500/10 px-3.5 py-1.5 text-xs font-medium text-blue-300">
              <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" aria-hidden="true" />
              AI Voice / Phone Agents • Enterprise Telephony
            </div>
            <h1 className="mt-6 text-4xl font-bold tracking-tight text-white sm:text-5xl lg:text-6xl">
              Build voice agents that{' '}
              <span className="bg-gradient-to-r from-blue-400 via-violet-400 to-cyan-400 bg-clip-text text-transparent">
                actually get work done
              </span>
            </h1>
            <p className="mt-6 max-w-2xl text-base leading-relaxed text-white/65 sm:text-lg">
              Production phone agents backed by real SIP/PSTN telephony, sub-second streaming ASR/TTS, RAG knowledge retrieval, mid-call tool execution, and warm human transfers with context preservation.
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-4">
              <Button
                size="lg"
                variant="primary"
                onClick={() => {
                  window.location.href = '/dashboard/agents/new';
                }}
              >
                Start Building →
              </Button>
              <Button
                size="lg"
                variant="secondary"
                onClick={() => {
                  if (onSeeHowItWorks) onSeeHowItWorks();
                }}
              >
                See How It Works
              </Button>
            </div>

            <div className="mt-10 flex flex-wrap items-center gap-2 text-xs">
              {HERO_STAGES.map((stage, idx) => (
                <React.Fragment key={stage.code}>
                  <span className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 font-medium text-white/80">
                    <span className="text-blue-400">{stage.code}</span>
                  </span>
                  {idx < HERO_STAGES.length - 1 && (
                    <span className="text-white/30" aria-hidden="true">→</span>
                  )}
                </React.Fragment>
              ))}
            </div>
          </div>

          <div className="lg:col-span-5">
            <GlassCard className="p-6 sm:p-8">
              <div className="flex items-center justify-between border-b border-white/10 pb-4">
                <div>
                  <div className="text-xs font-medium uppercase tracking-wider text-blue-300">
                    End-to-End Lifecycle
                  </div>
                  <div className="mt-1 text-base font-semibold text-white">
                    Build → Test → Deploy → Monitor
                  </div>
                </div>
                <span className="rounded-full border border-emerald-500/20 bg-emerald-500/10 px-2.5 py-1 text-[11px] font-medium text-emerald-300">
                  Real Backend
                </span>
              </div>
              <div className="mt-5 space-y-3">
                {HERO_STAGES.map((stage) => (
                  <div
                    key={stage.code}
                    className="flex items-center justify-between rounded-xl border border-white/10 bg-black/50 px-4 py-3"
                  >
                    <div className="flex items-center gap-3">
                      <span className="font-mono text-xs text-blue-400">{stage.step}</span>
                      <div>
                        <div className="text-xs font-semibold text-white">{stage.code}</div>
                        <div className="text-[11px] text-white/55">{stage.label}</div>
                      </div>
                    </div>
                    <span className="text-[10px] text-emerald-300">Verified</span>
                  </div>
                ))}
              </div>
              {stats && (
                <div className="mt-5 grid grid-cols-2 gap-3 border-t border-white/10 pt-4 text-xs">
                  <div>
                    <div className="text-white/40">Active Agents</div>
                    <div className="mt-0.5 font-semibold text-white">{stats.totalAgents ?? 0}</div>
                  </div>
                  <div>
                    <div className="text-white/40">Avg Turn Latency</div>
                    <div className="mt-0.5 font-semibold text-white">
                      {stats.avgLatencyMs ? `${stats.avgLatencyMs}ms` : 'Real-time'}
                    </div>
                  </div>
                </div>
              )}
            </GlassCard>
          </div>
        </div>
      </div>
    </section>
  );
}

export default VoiceAgentsHero;
