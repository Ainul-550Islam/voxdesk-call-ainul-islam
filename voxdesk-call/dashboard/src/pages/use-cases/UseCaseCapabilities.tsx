import React from 'react';
import type { UseCaseCapability } from '../../types/use-case';

interface Props {
  capabilities: UseCaseCapability[];
  className?: string;
}

export function UseCaseCapabilities({ capabilities, className = '' }: Props) {
  if (!capabilities || capabilities.length === 0) {
    return (
      <div className={`rounded-[20px] border border-dashed border-white/10 bg-white/[0.02] p-6 text-center ${className}`}>
        <div className="text-xs text-white/40">No capabilities configured</div>
        <div className="mt-1 text-[11px] text-white/30">Real capabilities only when backend provides verified list.</div>
      </div>
    );
  }
  return (
    <div className={`rounded-[20px] border border-white/10 bg-white/[0.03] p-6 ${className}`}>
      <div className="text-sm font-medium text-white">Capabilities</div>
      <div className="mt-1 text-xs text-white/50">Mapped to backend capabilities — real data only</div>
      <div className="mt-6 grid gap-3 sm:grid-cols-2">
        {capabilities.map((cap) => (
          <div key={cap.id} className={`rounded-[14px] border p-4 ${cap.enabled ? 'border-white/15 bg-white/[0.04]' : 'border-white/5 bg-white/[0.02] opacity-60'}`}>
            <div className="flex items-start justify-between gap-2">
              <div className="text-sm font-medium text-white">{cap.title}</div>
              {cap.enabled ? <span className="rounded-full bg-emerald-500/15 text-emerald-300 border border-emerald-500/20 px-2 py-0.5 text-[10px]">Enabled</span> : <span className="rounded-full bg-white/5 text-white/40 border border-white/5 px-2 py-0.5 text-[10px]">Disabled</span>}
            </div>
            {cap.description && <div className="mt-1.5 text-xs text-white/50 leading-relaxed">{cap.description}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}

export default UseCaseCapabilities;
