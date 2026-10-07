import React, { useState } from 'react';
import type { SaveState } from '../../types/agent-builder';
export function PromptEditor({ value, onChange, saveState }: { value: string; onChange: (v:string)=>void; saveState: SaveState }){
  const [local,setLocal]=useState(value);
  const charCount=local.length;
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between"><h2 className="text-sm font-medium text-white">System Prompt</h2><div className="flex items-center gap-3 text-[11px] text-white/50"><span>{charCount} chars</span><span aria-live="polite" className="rounded-full bg-white/10 px-2 py-1">{saveState}</span></div></div>
      <textarea value={local} onChange={(e)=>{ setLocal(e.target.value); onChange(e.target.value); }} className="w-full min-h-[300px] rounded-xl border border-white/10 bg-white/[0.05] p-4 text-sm text-white placeholder-white/40 focus:border-blue-500/50 focus:outline-none focus:ring-2 focus:ring-blue-500/20" placeholder="You are a helpful voice assistant... Use variables {{customer_name}} etc only if backend supports." />
      <div className="text-[11px] text-white/40">Ctrl+S to save • Variables: {'{{customer_name}}'} {'{{company}}'} • Only backend-supported variables allowed</div>
      <div className="flex gap-2"><button onClick={()=>{ setLocal(value); onChange(value); }} className="rounded-xl border border-white/10 px-3 py-1.5 text-xs text-white/70">Reset</button></div>
    </div>
  );
}
export default PromptEditor;
