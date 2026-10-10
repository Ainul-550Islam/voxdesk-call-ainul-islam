import React from 'react';
import type { UseCaseWorkflowStep } from '../../types/use-case';

interface Props {
  steps: UseCaseWorkflowStep[];
  className?: string;
}

export function UseCaseProcessTimeline({ steps, className = '' }: Props) {
  if (!steps || steps.length === 0) {
    return (
      <div className={`rounded-[16px] border border-dashed border-white/10 bg-white/[0.02] p-6 text-center ${className}`}>
        <div className="text-xs text-white/40">Workflow details not configured</div>
        <div className="mt-1 text-[11px] text-white/30">Real workflow data only when backend provides verified steps.</div>
      </div>
    );
  }
  const sorted = [...steps].sort((a, b) => a.order - b.order);
  return (
    <div className={`relative ${className}`} role="list" aria-label="Workflow steps">
      <div className="absolute left-[15px] top-0 bottom-0 w-px bg-gradient-to-b from-white/20 via-white/10 to-transparent" aria-hidden="true" />
      <div className="space-y-6">
        {sorted.map((step, idx) => (
          <div key={`${step.order}-${step.title}`} role="listitem" className="relative flex gap-4">
            <div className="relative z-10 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-white/15 bg-black text-xs font-medium text-white">
              {step.order}
            </div>
            <div className="min-w-0 flex-1 rounded-[14px] border border-white/10 bg-white/[0.03] p-4">
              <div className="text-sm font-medium text-white">{step.title}</div>
              <div className="mt-1 text-xs leading-relaxed text-white/60">{step.description}</div>
              {step.capabilities && step.capabilities.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {step.capabilities.map((cap) => (
                    <span key={cap} className="rounded-full bg-white/5 px-2 py-0.5 text-[10px] text-white/50 border border-white/5">{cap}</span>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default UseCaseProcessTimeline;
