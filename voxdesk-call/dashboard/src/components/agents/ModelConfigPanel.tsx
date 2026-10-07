import React, { useEffect, useState } from 'react';
import { getModelProviders } from '../../api/agents';
export function ModelConfigPanel({ modelId, onSelect }: { modelId?: string; onSelect: (id:string)=>void }){
  const [providers,setProviders]=useState<any[]>([]);
  const [loading,setLoading]=useState(true);
  useEffect(()=>{ (async()=>{ try{ const p=await getModelProviders(); setProviders(p);} catch{} finally{ setLoading(false);} })(); },[]);
  if(loading) return <div className="h-32 animate-pulse rounded-xl bg-white/5" />;
  return (
    <div className="space-y-4"><h2 className="text-sm font-medium text-white">Model Configuration</h2><p className="text-xs text-white/50">Only metadata from backend registry, no invented pricing. Provider abstraction.</p>
      <div className="grid gap-3">{providers.length===0?<div className="rounded-xl border border-white/10 bg-white/[0.03] p-4 text-xs text-white/40">No models — backend empty, not inventing. Example: openai/gpt-4o, anthropic/claude-3.5 — shown only as template.</div>:providers.map((pr:any)=>(<div key={pr.id} className="rounded-xl border border-white/10 bg-white/[0.03] p-4"><div className="text-sm text-white">{pr.name}</div><div className="mt-2 flex flex-wrap gap-2">{(pr.models||[]).map((m:any)=>(<button key={m.id} onClick={()=>onSelect(m.id)} className={`rounded-full px-3 py-1 text-xs ${modelId===m.id?'bg-white text-black':'bg-white/10 text-white/70'}`}>{m.name}</button>))}</div></div>))}</div>
    </div>
  );
}
export default ModelConfigPanel;
