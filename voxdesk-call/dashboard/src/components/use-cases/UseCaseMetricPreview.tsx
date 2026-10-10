import React from 'react';

interface Props {
  className?: string;
}

export function UseCaseMetricPreview({ className = '' }: Props) {
  return (
    <div className={`rounded-[16px] border border-dashed border-white/10 bg-white/[0.02] p-6 text-center ${className}`} role="status" aria-label="Metrics not configured">
      <div className="text-xs font-medium text-white/40">Metrics Preview</div>
      <div className="mt-2 text-[11px] text-white/30">No fake metrics — real data only when backend provides. Example/Template labeled explicitly.</div>
      <div className="mt-4 grid grid-cols-3 gap-3">
        {['Calls', 'Duration', 'Success'].map((label) => (
          <div key={label} className="rounded-xl bg-white/[0.03] p-3 border border-white/5">
            <div className="text-[10px] text-white/30">{label}</div>
            <div className="mt-1 text-xs text-white/20">—</div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default UseCaseMetricPreview;
