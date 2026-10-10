import React from 'react';
import { UseCaseProcessTimeline } from '../../components/use-cases/UseCaseProcessTimeline';
import type { UseCaseWorkflowStep } from '../../types/use-case';

interface Props {
  problem?: string;
  solution?: string;
  workflow: UseCaseWorkflowStep[];
  className?: string;
}

export function UseCaseWorkflow({ problem, solution, workflow, className = '' }: Props) {
  return (
    <div className={`space-y-8 ${className}`}>
      {(problem || solution) && (
        <div className="grid gap-6 md:grid-cols-2">
          {problem && (
            <div className="rounded-[20px] border border-white/10 bg-white/[0.03] p-6">
              <div className="text-xs font-medium uppercase tracking-wide text-white/40">Problem</div>
              <div className="mt-3 text-sm leading-relaxed text-white/70">{problem}</div>
            </div>
          )}
          {solution && (
            <div className="rounded-[20px] border border-white/10 bg-white/[0.03] p-6">
              <div className="text-xs font-medium uppercase tracking-wide text-white/40">Solution</div>
              <div className="mt-3 text-sm leading-relaxed text-white/70">{solution}</div>
            </div>
          )}
        </div>
      )}
      <div className="rounded-[20px] border border-white/10 bg-white/[0.03] p-6">
        <div className="text-sm font-medium text-white">How it works</div>
        <div className="mt-2 text-xs text-white/50">Inbound → Voice Agent → Knowledge/Tools → Business Action → Human Handoff — only supported nodes</div>
        <div className="mt-6">
          <UseCaseProcessTimeline steps={workflow} />
        </div>
      </div>
      <div className="rounded-[20px] border border-white/10 bg-black/50 p-6">
        <div className="text-xs font-medium uppercase tracking-wide text-white/40">Example Flow</div>
        <div className="mt-4 flex flex-wrap items-center gap-2 text-xs">
          {['Caller', 'Voice AI', 'Knowledge', 'Tools', 'Business System', 'Human'].map((node, idx, arr) => (
            <React.Fragment key={node}>
              <span className="rounded-full bg-white/5 border border-white/10 px-3 py-1.5 text-white/70">{node}</span>
              {idx < arr.length - 1 && <span className="text-white/20" aria-hidden="true">→</span>}
            </React.Fragment>
          ))}
        </div>
      </div>
    </div>
  );
}

export default UseCaseWorkflow;
