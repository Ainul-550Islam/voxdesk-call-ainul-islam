import React, { useState } from 'react';
import { useAgentTest } from '../../hooks/useAgentTest';
import { VoiceOrb } from '../voice/VoiceOrb';
import { Waveform } from '../voice/Waveform';
export function AgentTestPanel({ agentId }: { agentId: string }){
  const { session, state, loading, error, isConfigured, start, stop, sendText } = useAgentTest(agentId);
  const [input,setInput]=useState('');
  if(!isConfigured) return <div className="rounded-xl border border-amber-500/20 bg-amber-500/10 p-4 text-sm text-amber-200">Testing unavailable — agent not configured. Publish or complete configuration first.</div>;
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between"><h2 className="text-sm font-medium text-white">Test Agent</h2><div className="flex items-center gap-2"><span className="text-xs text-white/50">{state}</span>{state==='IDLE'||state==='ENDED'?<button onClick={start} disabled={loading} className="rounded-xl bg-white px-4 py-2 text-xs font-medium text-black">Start</button>:<button onClick={stop} className="rounded-xl border border-white/20 px-4 py-2 text-xs text-white">Stop</button>}</div></div>
      <div className="flex flex-col items-center gap-4 rounded-2xl border border-white/10 bg-white/[0.03] p-8"><VoiceOrb state={state as any} /><Waveform active={state==='LISTENING'||state==='SPEAKING'} /></div>
      {error && <div className="rounded-xl border border-red-500/20 bg-red-500/10 p-3 text-xs text-red-300">{error}</div>}
      <div className="space-y-3 max-h-[300px] overflow-auto">{(session?.transcript||[]).map((t:any)=>(<div key={t.id} className={`rounded-xl p-3 text-sm ${t.role==='user'?'bg-white/10 text-white':'bg-blue-500/10 text-blue-100'}`}><div className="text-[11px] opacity-60">{t.role} • {new Date(t.timestamp).toLocaleTimeString()} {t.latency_ms?`• ${t.latency_ms}ms`:''}</div><div className="mt-1">{t.content}</div></div>))}</div>
      <div className="flex gap-2"><input value={input} onChange={e=>setInput(e.target.value)} onKeyDown={e=>{ if(e.key==='Enter'){ sendText(input); setInput(''); } }} placeholder="Type a message..." className="flex-1 rounded-xl border border-white/10 bg-white/[0.05] px-4 py-2.5 text-sm text-white" /><button onClick={()=>{ sendText(input); setInput(''); }} className="rounded-xl bg-white px-4 py-2 text-sm font-medium text-black">Send</button></div>
    </div>
  );
}
export default AgentTestPanel;
