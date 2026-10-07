import React from 'react';
export function CallHandlingPanel({ config, onChange }: { config: any; onChange: (c:any)=>void }){
  return (
    <div className="space-y-6"><h2 className="text-sm font-medium text-white">Call Handling</h2><p className="text-xs text-white/50">Only backend-supported fields — unsupported not fake controls.</p>
      <div className="space-y-4">
        <div><label className="text-xs text-white/70">Welcome Message</label><input value={config?.welcome_message||''} onChange={e=>onChange({ ...config, welcome_message:e.target.value })} className="mt-1 w-full rounded-xl border border-white/10 bg-white/[0.05] px-3 py-2 text-sm text-white" placeholder="Hello..." /></div>
        <div><label className="text-xs text-white/70">Transfer Number</label><input value={config?.transfer_number||''} onChange={e=>onChange({ ...config, transfer_number:e.target.value })} className="mt-1 w-full rounded-xl border border-white/10 bg-white/[0.05] px-3 py-2 text-sm text-white" placeholder="+1..." /></div>
        <div><label className="text-xs text-white/70">Voicemail Behavior</label><select value={config?.voicemail_behavior||'hangup'} onChange={e=>onChange({ ...config, voicemail_behavior:e.target.value })} className="mt-1 w-full rounded-xl border border-white/10 bg-black px-3 py-2 text-sm text-white"><option value="hangup">Hangup</option><option value="message">Leave message</option><option value="transfer">Transfer</option></select></div>
      </div>
    </div>
  );
}
export default CallHandlingPanel;
