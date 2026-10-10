import React from 'react';
import type { UseCaseIntegration } from '../../types/use-case';

interface Props {
  integrations: UseCaseIntegration[];
  className?: string;
}

export function UseCaseIntegrationPreview({ integrations, className = '' }: Props) {
  const verified = integrations.filter(i => i.verified);
  if (!integrations || integrations.length === 0) {
    return (
      <div className={`rounded-[20px] border border-dashed border-white/10 bg-white/[0.02] p-6 text-center ${className}`}>
        <div className="text-xs text-white/40">No integrations configured</div>
        <div className="mt-1 text-[11px] text-white/30">Verified integrations only — real backend data.</div>
      </div>
    );
  }
  return (
    <div className={`rounded-[20px] border border-white/10 bg-white/[0.03] p-6 ${className}`}>
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium text-white">Integrations</div>
        <div className="text-[11px] text-white/40">{verified.length} verified</div>
      </div>
      <div className="mt-1 text-xs text-white/50">Verified only — no invented integrations</div>
      <div className="mt-6 grid gap-3 sm:grid-cols-2">
        {integrations.map((integration) => (
          <div key={integration.id} className={`rounded-[14px] border p-4 ${integration.verified ? 'border-emerald-500/20 bg-emerald-500/5' : 'border-white/10 bg-white/[0.02]'}`}>
            <div className="flex items-center gap-2">
              <div className="h-8 w-8 rounded-lg bg-white/10 flex items-center justify-center text-xs">{integration.name[0]}</div>
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-white truncate">{integration.name}</div>
                <div className="text-[11px] text-white/50 truncate">{integration.category || 'Integration'}</div>
              </div>
              {integration.verified && <span className="rounded-full bg-emerald-500/15 text-emerald-300 border border-emerald-500/20 px-2 py-0.5 text-[10px]">Verified</span>}
            </div>
            {integration.description && <div className="mt-2 text-xs text-white/50 line-clamp-2">{integration.description}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}

export default UseCaseIntegrationPreview;
