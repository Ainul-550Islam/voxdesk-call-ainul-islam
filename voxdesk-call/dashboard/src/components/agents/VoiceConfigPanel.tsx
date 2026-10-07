import React, { useEffect, useState } from 'react';
import { getVoiceProviders } from '../../api/agents';
export function VoiceConfigPanel({ voiceId, onSelect }: { voiceId?: string; onSelect: (id:string)=>void }){
  const [providers,setProviders]=useState<any[]>([]);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState<string|null>(null);
  useEffect(()=>{ (async()=>{ try{ const p=await getVoiceProviders(); setProviders(p);} catch(e:any){ setError(e?.message||'Voice catalog unavailable'); } finally{ setLoading(false);} })(); },[]);
  if(loading) return <div className="animate-pulse h-32 rounded-xl bg-white/5" />;
  if(error) return <div className="rounded-xl border border-red-500/20 bg-red-500/10 p-4 text-sm text-red-300">{error} <button onClick={()=>window.location.reload()} className="ml-2 underline">Retry</button></div>;
  return (
    <div className="space-y-4"><h2 className="text-sm font-medium text-white">Voice Configuration</h2><p className="text-xs text-white/50">Backend-driven catalog, no hardcoded fake voices. Preview via secure backend.</p>
      <div className="grid gap-3">{providers.length===0?<div className="text-xs text-white/40">No voices — backend returns empty, not inventing. Configure provider in backend.</div>:providers.map((p:any)=>(<div key={p.id||p.name} className="rounded-xl border border-white/10 bg-white/[0.03] p-4"><div className="text-sm text-white">{p.name||p.id}</div><div className="mt-2 flex flex-wrap gap-2">{(p.voices||[]).map((v:any)=>(<button key={v.id} onClick={()=>onSelect(v.id)} className={`rounded-full px-3 py-1 text-xs ${voiceId===v.id?'bg-white text-black':'bg-white/10 text-white/70 hover:bg-white/20'}`}>{v.name}</button>))}</div></div>))}</div>
    </div>
  );
}
export default VoiceConfigPanel;
