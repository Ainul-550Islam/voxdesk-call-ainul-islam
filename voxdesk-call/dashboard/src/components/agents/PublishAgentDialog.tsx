import React from 'react';
import type { ValidationResult } from '../../types/agent-builder';
export function PublishAgentDialog({ open, onClose, validation, onPublish }: { open: boolean; onClose: ()=>void; validation: ValidationResult|null; onPublish: ()=>void }){
  if(!open) return null;
  return (
    <div role="dialog" aria-modal="true" className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur p-4">
      <div className="w-full max-w-lg rounded-2xl border border-white/10 bg-[#0a0a0a] p-6">
        <h2 className="text-base font-medium text-white">Publish Agent</h2><p className="mt-1 text-xs text-white/50">Draft → Validate → Confirmation → Backend publish → Published. Field-level errors shown, never silently publish.</p>
        <div className="mt-4 space-y-3">{validation?.errors?.length?<div className="space-y-2">{validation.errors.map((e,i)=>(<div key={i} className="rounded-xl border border-red-500/20 bg-red-500/10 p-3 text-xs text-red-300"><span className="font-medium">{e.field}:</span> {e.message}</div>))}</div>:<div className="text-xs text-emerald-300">✓ Validation passed — ready to publish</div>}{validation?.warnings?.map((w,i)=>(<div key={i} className="rounded-xl border border-amber-500/20 bg-amber-500/10 p-3 text-xs text-amber-300">{w.field}: {w.message}</div>))}</div>
        <div className="mt-6 flex justify-end gap-2"><button onClick={onClose} className="rounded-xl border border-white/10 px-4 py-2 text-xs text-white/70">Cancel</button><button onClick={onPublish} disabled={validation && !validation.valid} className="rounded-xl bg-blue-600 px-4 py-2 text-xs font-medium text-white hover:bg-blue-500 disabled:opacity-50">Publish</button></div>
      </div>
    </div>
  );
}
export default PublishAgentDialog;
