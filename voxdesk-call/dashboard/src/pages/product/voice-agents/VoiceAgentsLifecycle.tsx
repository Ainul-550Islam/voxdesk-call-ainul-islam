
/**
 * VoiceAgentsLifecycle.tsx — Build → Test → Deploy → Monitor lifecycle overview
 */
import React, { useState, useCallback, useMemo } from 'react';
import { GlassCard } from '../../../components/ui/GlassCard';
import { Button } from '../../../components/ui/Button';

interface LifecycleStep {
  id: string;
  order: number;
  title: string;
  description: string;
  shortTitle: string;
  icon: string;
  color: string;
  features: string[];
  cta?: { label: string; href: string };
}

interface Props {
  steps: LifecycleStep[];
  activeId: string;
  onChange: (id: string) => void;
  activeStep: LifecycleStep;
}

export function VoiceAgentsLifecycle({ steps, activeId, onChange, activeStep }: Props) {
  const [expanded, setExpanded] = useState<string | null>(activeId);

  const handleStepClick = useCallback((id: string) => {
    onChange(id);
    setExpanded(id);
  }, [onChange]);

  const sortedSteps = useMemo(() => [...steps].sort((a,b) => a.order - b.order), [steps]);

  return (
    <section id="lifecycle" className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-16 sm:py-24">
      <div className="max-w-3xl">
        <h2 className="text-3xl font-bold tracking-tight text-white sm:text-4xl">Build → Test → Deploy → Monitor → Improve</h2>
        <p className="mt-4 text-[15px] leading-relaxed text-white/60">
          Voice-agent lifecycle overview. Persisted configuration and deterministic tests are distinct from live provider calls; provider availability is deployment-specific.
        </p>
      </div>

      <div className="mt-12 grid gap-8 lg:grid-cols-3">
        <div className="lg:col-span-1">
          <div className="sticky top-24 space-y-2">
            {sortedSteps.map((step) => {
              const isActive = step.id === activeId;
              return (
                <button
                  key={step.id}
                  onClick={() => handleStepClick(step.id)}
                  aria-pressed={isActive}
                  className={`w-full text-left rounded-[16px] border p-4 transition-all ${isActive ? 'bg-white text-black border-white shadow-lg' : 'bg-white/[0.03] text-white/70 border-white/10 hover:bg-white/[0.05] hover:text-white'}`}
                >
                  <div className="flex items-center gap-3">
                    <div className={`flex h-8 w-8 items-center justify-center rounded-full text-sm ${isActive ? 'bg-black text-white' : 'bg-white/10 text-white'}`}>{step.order}</div>
                    <div>
                      <div className="text-sm font-medium">{step.shortTitle}</div>
                      <div className={`text-[11px] ${isActive ? 'text-black/60' : 'text-white/40'}`}>{step.icon} {step.title.split('—')[0].trim()}</div>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        <div className="lg:col-span-2">
          <GlassCard className="p-8">
            <div className="flex items-start gap-4">
              <div className={`flex h-12 w-12 items-center justify-center rounded-xl bg-gradient-to-br ${activeStep.color} text-xl`}>{activeStep.icon}</div>
              <div className="min-w-0 flex-1">
                <h3 className="text-xl font-semibold text-white">{activeStep.title}</h3>
                <p className="mt-3 text-sm leading-relaxed text-white/60">{activeStep.description}</p>
                
                <div className="mt-6">
                  <div className="text-xs font-medium uppercase tracking-wide text-white/40">Lifecycle areas</div>
                  <div className="mt-3 grid gap-2 sm:grid-cols-2">
                    {activeStep.features.map((feat) => (
                      <div key={feat} className="flex items-center gap-2 rounded-xl bg-white/[0.03] border border-white/5 px-3 py-2.5 text-xs text-white/70">
                        <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" aria-hidden="true" />
                        {feat}
                      </div>
                    ))}
                  </div>
                </div>

                <div className="mt-8 rounded-[16px] border border-white/10 bg-black/50 p-4">
                  <div className="text-xs font-medium text-white">API surface and verification boundary</div>
                  <div className="mt-3 space-y-2 text-[11px] font-mono text-white/50">
                    {activeStep.id === 'build' && (
                      <>
                        <div>POST /api/agents — Create draft</div>
                        <div>PUT /api/agents/{'{'}id{'}'}/builder — Configure voice, knowledge, tools</div>
                        <div>POST /api/knowledge-base — Add knowledge sources</div>
                      </>
                    )}
                    {activeStep.id === 'test' && (
                      <>
                        <div>POST /api/agents/{'{'}id{'}'}/test — Simulate call</div>
                        <div>GET /api/calls/{'{'}id{'}'}/transcript-summary — Persisted transcript summary</div>
                        <div>POST /api/calls/{'{'}id{'}'}/dtmf — Test DTMF</div>
                      </>
                    )}
                    {activeStep.id === 'deploy' && (
                      <>
                        <div>POST /api/phone-numbers — Assign number</div>
                        <div>POST /api/calls — Provider-dependent outbound request</div>
                        <div>POST /api/sip-trunks — SIP integration</div>
                      </>
                    )}
                    {activeStep.id === 'monitor' && (
                      <>
                        <div>GET /api/calls/{'{'}id{'}'}/monitor — Monitoring control session; not proof of live media</div>
                        <div>GET /api/calls/{'{'}id{'}'}/analytics — Call analytics response</div>
                        <div>Live media/event streaming — requires a configured runtime</div>
                      </>
                    )}
                    {activeStep.id === 'improve' && (
                      <>
                        <div>POST /api/agents/{'{'}id{'}'}/versions — Version control</div>
                        <div>POST /api/experiments — Persist a weighted draft; live call assignment is not connected</div>
                        <div>GET /api/analytics/feedback — Real feedback</div>
                      </>
                    )}
                  </div>
                </div>

                {activeStep.cta && (
                  <div className="mt-8">
                    <Button variant="primary" size="sm" onClick={() => window.location.href = activeStep.cta!.href} className="rounded-xl bg-white px-5 py-2.5 text-xs font-medium text-black">
                      {activeStep.cta.label} →
                    </Button>
                  </div>
                )}
              </div>
            </div>
          </GlassCard>

          <div className="mt-8 grid gap-4 sm:grid-cols-2">
            <div className="rounded-[16px] border border-white/10 bg-white/[0.03] p-5">
              <div className="text-xs font-medium text-white">Call Routing</div>
              <div className="mt-2 text-[11px] text-white/50">Configured IVR, queue, conditional, and skills-based routing policies</div>
              <div className="mt-3 text-xs text-white/60">POST /api/calls/{'{'}id{'}'}/transfer — Warm transfer with context preservation</div>
            </div>
            <div className="rounded-[16px] border border-white/10 bg-white/[0.03] p-5">
              <div className="text-xs font-medium text-white">Compliance</div>
              <div className="mt-2 text-[11px] text-white/50">Configured DNC, calling-window, consent, recording, and PII-redaction controls</div>
              <div className="mt-3 text-xs text-white/60">GET /api/calls/{'{'}id{'}'}/compliance — Call compliance status</div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

export default VoiceAgentsLifecycle;
